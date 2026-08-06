# Scenario 5: USER I/O from Facedancer

Child of the GSG scenarios master. Reference: `docs/gsg-scenarios.md` §5.

## What it is

Reach the six USER LEDs, the USER button and USER Pmod A pins from Python while a
Facedancer emulation is running — so an emulation can flash an LED on each bus reset, or
wait for a button press before typing. Pmod B is unavailable: it carries the SoC UART and
JTAG.

## What implements it upstream

- Gateware: the Facedancer bitstream's `leds` and `gpio0`/`gpio1` CSR peripherals.
- Firmware: `cynthion: firmware/moondancer/src/gcp/leds.rs` and `gcp/gpio.rs` — two GCP
  classes.
- Host: `cynthion: cynthion/python/src/interfaces/led.py` and `interfaces/gpio.py`,
  reached through `cynthion.Cynthion()`. Pin map in
  `boards/cynthion_moondancer.py::GPIO_MAPPINGS` — PMOD A1–A10 plus `USER`.

## Hardware it needs

Two USB cables (CONTROL and TARGET) and, for the Pmod example, a breadboard with a switch
and an LED. Nothing exotic.

## What porting it would require

This is the least USB-dependent scenario in the whole set, and most of it already exists.

- **LEDs:** `top.py` drives the six USER LEDs from an `amaranth_soc.gpio`
  block at `GPIO_BASE`. Present.
- **USER button and Pmod A:** the platform declares them; the GPIO peripheral is upstream
  `amaranth_soc.gpio`, unmodified, with per-pin direction control — which is what the Pmod
  example needs.
- **What is missing is the path from the host to those registers.** Upstream reaches them
  over libgreat/GCP, which means this scenario inherits scenario 3's transport even though
  it needs none of scenario 3's USB peripherals.

That inheritance is the interesting part. **This scenario could be delivered over our
existing console instead** — the CDC-ACM `USBSerialDevice` on AUX already gives the host a
tty to the firmware, and the firmware already has a shell that `./dev.py test` drives. A
`leds` and `gpio` command pair on that shell would give a Python user the same capability
without a single USB peripheral existing.

That would not be compatible with `cynthion.Cynthion()`, and the tradeoff should be named:
compatibility with upstream's host API versus being able to ship it now. If the GCP
transport is built for scenario 3 anyway, this becomes near-free at that point.

## How it would be tested

- **QEMU: yes, for the command layer.** The shell already runs under `-M virt` and
  `./dev.py test` already asserts on its output. Register writes to a GPIO block that
  QEMU does not have will not work, but the parse-and-dispatch half will.
- **Simulation: yes.** `soc_board_sim` already exists in the fast tier and covers the board
  peripheral block. Extending it to assert LED and GPIO register behaviour is incremental.
- **Hardware: for the final claim only** — an LED visibly lighting, a button read. Cheap,
  and already the kind of thing `./dev.py test-board` does.

The best test coverage of any scenario here, because none of it needs a packet.

## Verdict

**Portable.** The peripherals exist; only the host-facing transport is in question. Worth
doing early if we decide the console shell is an acceptable interface, and near-free later
if we do not.
