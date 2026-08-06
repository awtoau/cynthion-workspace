# Making the test gateware reusable by the CPU build

Written because the test code currently cannot carry forward. Eleven bitstream
directories, each a whole-chip design; **17 of them bound to
`JTAGRegisterInterface` against 5 with a bus interface.** A peripheral wired to a
host-driven JTAG register file cannot be attached to a CPU, so today every test
is a throwaway.

This is the plan to fix that, and the mastership model it depends on.

## Mastership: one bus, two possible masters, never both

The tempting answer -- CPU and sideband as co-masters through an arbiter -- does
not work. Locking is the problem: two masters that can both write a peripheral
need a lock protocol, and neither a 6-LED display nor a flash controller has one.

So the model is **exactly one master at a time**, decided by build:

| build | bus master | sideband role |
|---|---|---|
| **test bitstream** (no CPU) | **the sideband responder** | it *is* the master |
| **CPU build** | **the CPU** | an I2C peripheral *on* the CPU's bus |

That second row is the important one. When a CPU is present the sideband stops
being a master and becomes a **device the CPU owns**, addressed like any other
peripheral, with an interrupt line. The host still reaches it -- Apollo still
talks FPGA_ADV -- but writes now go through the CPU, which is the only thing that
knows whether a peripheral is mid-transaction.

**That also makes the sideband the EIC replacement** (#95): as a CPU peripheral with
an interrupt it carries the port request, the commands and user data, and the
EIC/UART mode distinction stops mattering. The two modes, and why the port request
and the command traffic contend for the wire, are in
[`chips/cynone-sideband.md`](chips/cynone-sideband.md#8-the-second-job-the-control-port-request).

## One I2C controller, three bus pin-sets

r1.4 has **three physically separate I2C buses**, and the reason is forced rather
than chosen: **both FUSB302Bs sit at address `0x22`**, so they cannot be
distinguished on one bus. The bus table, the pins and the addresses are in
[`hardware.md`](hardware.md#the-buses); the chip notes are
[`chips/fusb302b-type-c.md`](chips/fusb302b-type-c.md) and
[`chips/pac1954-power-monitor.md`](chips/pac1954-power-monitor.md).

The consequence for this plan: "just use one bus" is not available in hardware,
but one *controller* is. A single I2C master with a 2-bit bus select driving three
pin-sets is the multiplexed master #98 already asks for, and it replaces three
replicated controllers with one plus a mux.

The interrupt lines get a PLIC source each, and the level-sensitive trap that a
shared one would have carried is described in
[`chips/fusb302b-type-c.md`](chips/fusb302b-type-c.md#interrupts). Not urgent: PD
negotiation is not on the critical path.

**Done, and on silicon** (#121). `gateware/soc/i2c_mux.py` is the select and
the four Type-C signals; `gateware/soc/i2c_master.py` gained an `idle` output
so the select cannot move underneath a transfer. The two `int` lines were OR-ed
onto one PLIC source here, **and #135 gave each its own** — one controller does
mean one device at a time on the bus, but that says nothing about which device the
handler should be told to service, and the PLIC had 27 spare sources. See
[`architecture.md`](architecture.md) decision 8. The handler still *masks* rather than
clears: clearing needs a millisecond of I2C on the controller the foreground is
also using, which is not a thing an interrupt handler may do. Normal context
clears the device that asserted and re-enables its source.
Note the earlier `gateware/probes/i2c/multiplexed.py` was never on silicon and is
superseded.

**On presenting the LEDs as a fake I2C device:** attractive for uniformity and
wrong here. The LEDs are six wires in the same fabric -- wrapping them in a
serial protocol adds a state machine, a byte-time of latency and an error path to
something that is currently one combinational assignment. Uniformity is worth
paying for at a *bus* boundary, not inside the chip. The LEDs should be a CSR
register, same as everything else on the bus.

## The port: peripherals become bus components

Each test peripheral grows a CSR interface and loses its `JTAGRegisterInterface`.
The register file does not disappear -- it becomes a **bridge**, so the same
peripheral is reachable either way:

    host over JTAG  ->  JTAG-to-Wishbone bridge  ->|
                                                   |-> peripheral (CSR)
    CPU                                         ->|

That is what makes one implementation serve both builds. It is also how the
SoC already reaches its console this way, so the pattern is proven in this tree
rather than proposed.

Order, cheapest and most-load-bearing first:

1. **I2C master, multiplexed** (#98) -- unblocks FUSB302B and PAC1954 together.
2. **LEDs** as a CSR register with the existing override/release semantics.
3. **Flash** (QSPI) -- already has the most bus-shaped interface of the group.
4. **HyperRAM** (#90) -- the largest, and the one with a real timing question.

## Confirmed scope

All four requested, with the sizing stated because they compete for one 12F:

**All three USB ports as 480 Mbps CDC.** Given. This is the largest item -- three
device stacks against 24288 LUTs with a CPU wanting ~6000 -- and the shipping
bitstream will configure the ports differently. The point is proving all three
*can* come up, not that one image uses them that way.

**HyperRAM ID and serial readback.** Small, and closes a real gap: DEVICES
currently reports `hyperram absent` as a presence bit with no identity behind it.
The registers themselves are already read and decoded from a standalone bitstream
— [`chips/w956a8-hyperram.md`](chips/w956a8-hyperram.md) — so what is missing is
the CPU-side path, not the knowledge.

**FUSB302B and die temperature over the sideband.** Small, because the gateware
exists -- `gateware/probes/pins/fusb302_id.py` already reads both controllers. What is
missing is sideband *commands*, not blocks; the opcode map and what adding to it
costs are in [`chips/cynone-sideband.md`](chips/cynone-sideband.md#4-commands). Temperature needs the `DTR`
primitive instantiated, which it currently is not anywhere.

**Flash and HyperRAM benchmarking driven by the CPU.** The measurements that
motivated the RISC-V work. **Blocked** on the silent-SoC problem: both CPUs
enumerate and neither prints, cause not established
(#209).

## What this does not resolve

**Whether it all fits.** Three USB stacks plus a CPU plus caches plus HyperRAM on
an LFE5U-12F is not obviously feasible, and nothing here has been synthesised
together. The area numbers to beat: best CPU config measured at 6342 LUT and
14 of 56 BRAM; the sideband test bitstream at 123920 bytes builds today with the
responder, flash and power monitor.

**The silent SoC.** Everything CPU-driven waits on it, and it is the one item in
this plan with no established cause rather than an unstarted task.
