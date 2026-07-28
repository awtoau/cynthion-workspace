#!/usr/bin/env python3
#
# Test bitstream for the FPGA_ADV command protocol.
# See awtoau/cynthion-workspace#68 and
# docs/apollo_samd11_mcu/fpga-adv-command-protocol.md.
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Instantiates SidebandResponder on FPGA_ADV with the six board LEDs wired to its
status output, so the link can be diagnosed by looking at the board.

The LEDs matter here: a silent link gives no clue whether the FPGA never saw
the command, saw it and rejected it, or answered and the master lost the reply.
These distinguish all three without a debug session.

Also exposes a JTAG register interface, so the responder can be checked from
the host even when the sideband itself is not working -- an independent path to
the same state, which is what makes a broken link diagnosable rather than
merely broken.
"""

from amaranth                            import Cat, Const, Mux, Signal, Elaboratable, Module
from luna.gateware.interface.i2c         import I2CRegisterInterface
from luna.gateware.architecture.car      import LunaECP5DomainGenerator
from luna.gateware.interface.jtag        import JTAGRegisterInterface

from apollo_fpga.gateware.sideband       import SidebandResponder


CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}

# Register 0 is reserved by JTAGRegisterInterface for size auto-negotiation.
REGISTER_ID        = 1
REGISTER_LEDS      = 2   # read the responder's status bits
REGISTER_RX_COUNT  = 3   # command bytes received, for liveness checking
REGISTER_POWER_LO  = 4   # low 32 bits of the simulated power payload
REGISTER_POWER_HI  = 5   # next 32 bits
REGISTER_EDGES     = 6   # raw FPGA_ADV edge count, independent of framing
REGISTER_PIN_LEVEL = 7   # current FPGA_ADV level, for a static check
REGISTER_RX_BYTES  = 8   # count of bytes the UART receiver framed successfully
REGISTER_LAST_RX   = 9   # the last byte it framed, whatever it was

APPLET_ID = 0x53424E44   # "SBND"


class SidebandTest(Elaboratable):
    """ Sideband responder with LEDs and a JTAG view of its state. """

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        m.submodules.responder = responder = SidebandResponder(
            clk_freq_hz=60e6, baud=115200)

        #
        # FPGA_ADV.
        #
        # The pin is declared dir="o" by the platform, so this drives it
        # push-pull. Apollo never enables its own driver while the protocol is
        # active, which is what keeps the wire contention-free -- see §2.1 of
        # the sideband design document.
        #
        # No hasattr guards. An earlier version used them, and when the
        # platform still declared this pin dir="o" they silently degraded to
        # tying responder.rx to a constant 1 and driving the pin push-pull
        # forever -- so the receiver was never connected to the wire and Apollo
        # could not pull it low against us. It failed quietly instead of
        # loudly. If the platform regresses to dir="o", this now raises.
        pad = platform.request("int")
        m.d.comb += [
            responder.rx.eq(pad.i),
            pad.o.eq(responder.tx),
            # Release the line whenever we are not replying, so Apollo can
            # drive it. Both ends pull up, so it idles high either way.
            pad.oe.eq(responder.tx_active),
        ]


        #
        # Live PAC1954 poller.
        #
        # Reads the manufacturer ID over I2C on a loop and blinks an LED on
        # every successful read. That makes the I2C link visible on the board
        # independently of the sideband: if this LED flashes, the FPGA is
        # talking to the power monitor, whatever the UART is doing.
        #
        # Confirmed on r1.4: the device answers at 0x10 (ADDRSEL strapped to
        # GND per DS20006539B Table 6-1), MANUFACTURER_ID reads 0x54.
        #
        pmon = platform.request("power_monitor")

        # PinsN, so driving .o low de-asserts power-down and enables the part.
        m.d.comb += pmon.pwrdn.o.eq(0)
        m.d.comb += [pmon.slow.o.eq(0), pmon.slow.oe.eq(1)]

        m.submodules.i2c = i2c = I2CRegisterInterface(
            pads=pmon, period_cyc=600, address=0x10, clk_stretch=True)

        pmon_value = Signal(8)
        pmon_blink = Signal()
        poll_timer = Signal(24)

        m.d.comb += [
            i2c.address.eq(0xFE),   # MANUFACTURER_ID
            i2c.size.eq(1),
        ]

        with m.FSM(name="pmon"):
            with m.State("WAIT"):
                m.d.sync += poll_timer.eq(poll_timer + 1)
                # Top bit of a 24-bit counter at 60 MHz is ~140 ms, so the
                # blink is slow enough to see rather than a blur.
                with m.If(poll_timer[23]):
                    m.d.sync += poll_timer.eq(0)
                    m.d.comb += i2c.read_request.eq(1)
                    m.next = "READ"

            with m.State("READ"):
                with m.If(i2c.done):
                    m.d.sync += [
                        pmon_value.eq(i2c.read_data),
                        # Toggle only on the expected value: a blink then means
                        # "the PAC1954 answered correctly", not merely "the I2C
                        # state machine completed".
                        pmon_blink.eq(Mux(i2c.read_data[:8] == 0x54,
                                          ~pmon_blink, pmon_blink)),
                    ]
                    m.next = "WAIT"

        #
        # Power payload for CMD_POWER.
        #
        # Still the fixed test pattern for the payload bytes -- distinguishable
        # per byte so a transposition shows up as a wrong value rather than a
        # plausible one -- but with the live manufacturer ID in the low byte,
        # so a POWER response proves the I2C read is real.
        #
        power = Signal(128)
        m.d.comb += power.eq(0xF001_DDEE_BBCC_99AA_7788_5566_3344_1100
                             | pmon_value)
        m.d.comb += responder.power_data.eq(power)

        #
        # Status LEDs. Inverted: the platform declares these with LEDResources(invert=True),
        # so driving 0 lights the LED. Without the complement an idle responder
        # (leds == 0) lights all six, which reads as "everything is happening"
        # when in fact nothing is.
        # LED 5 shows the PAC1954 poll rather than the responder's
        # unknown-command flag: an independent, always-running heartbeat is
        # more useful on the bench than a flag that only matters after a bad
        # command.
        # The six LEDs run red, orange, yellow, green, blue, violet (index 0
        # to 5). Describe states by colour, never by number: the board carries
        # no labels, so counting positions at the bench is impractical.
        #
        # Deliberately not one-bit-per-signal. Two states, unmistakable at a
        # glance across the room:
        #
        #   RED blinking, rest dark  -- FPGA alive (PAC1954 polling), but
        #                               nothing is talking to it
        #   ALL SIX lit              -- a sideband command is being handled
        #
        # The per-signal detail is still readable over JTAG in REGISTER_LEDS
        # when that level of detail is actually wanted.
        active  = responder.leds[0] | responder.leds[1]
        display = Signal(6)
        m.d.comb += display.eq(Mux(active, 0b111111, Cat(pmon_blink, Const(0, 5))))
        leds = Cat([platform.request("led", i, dir="o").o for i in range(6)])
        m.d.comb += leds.eq(display)

        #
        # JTAG view of the same state, so the responder can be inspected even
        # when the sideband link itself is not working.
        #
        # Count responses rather than received bytes: the heartbeat bit toggles
        # once per completed reply, so its edges count transactions that ran to
        # completion. A byte counter would also count bytes that were received
        # but never answered, which is the less useful number.
        rx_count  = Signal(32)
        last_beat = Signal()
        m.d.sync += last_beat.eq(responder.leds[4])
        with m.If(responder.leds[4] != last_beat):
            m.d.sync += rx_count.eq(rx_count + 1)

        registers.add_read_only_register(REGISTER_LEDS, read=responder.leds)
        registers.add_read_only_register(REGISTER_RX_COUNT, read=rx_count)
        registers.add_read_only_register(REGISTER_POWER_LO, read=power[:32])
        registers.add_read_only_register(REGISTER_POWER_HI, read=power[32:64])

        #
        # Raw edge counter on FPGA_ADV.
        #
        # Deliberately upstream of the UART receiver: it counts transitions
        # whatever their timing, so it separates "Apollo drives nothing" from
        # "Apollo drives something the receiver cannot frame". The first shows
        # a static count; the second shows it climbing while responses stay
        # at zero.
        #
        adv_sync  = Signal(2)
        adv_edges = Signal(32)
        m.d.sync += adv_sync.eq(Cat(responder.rx, adv_sync[0]))
        with m.If(adv_sync[0] != adv_sync[1]):
            m.d.sync += adv_edges.eq(adv_edges + 1)

        registers.add_read_only_register(REGISTER_EDGES, read=adv_edges)
        registers.add_read_only_register(REGISTER_PIN_LEVEL, read=responder.rx)

        #
        # Raw receiver instrumentation.
        #
        # Counts every byte the UART framed, and latches the last one, whether
        # or not the responder recognised it as a command. That separates
        # "nothing arrived" from "something arrived but was not a valid
        # command" -- the edge counter alone cannot tell those apart, and the
        # responder's own state only reflects bytes it accepted.
        #
        rx_bytes = Signal(32)
        last_rx  = Signal(8)
        with m.If(responder.rx_strobe):
            m.d.sync += [rx_bytes.eq(rx_bytes + 1), last_rx.eq(responder.rx_byte)]

        registers.add_read_only_register(REGISTER_RX_BYTES, read=rx_bytes)
        registers.add_read_only_register(REGISTER_LAST_RX, read=last_rx)

        return m
