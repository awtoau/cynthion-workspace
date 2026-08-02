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

**That also makes the sideband the EIC replacement** (#95). Today FPGA_ADV
carries edge counts in EIC mode and commands in UART mode. As a CPU peripheral
with an interrupt it carries both, plus user data, and the mode distinction stops
mattering.

## One I2C controller, three bus pin-sets

r1.4 has **three physically separate I2C buses**, which is not obvious and
changes the design:

| resource | pins | device |
|---|---|---|
| `target_type_c` | A4 / C4 | FUSB302B @ `0x22` |
| `aux_type_c` | H12 / G14 | FUSB302B @ `0x22` |
| `power_monitor` | D7 / C7 | PAC1954 @ `0x10` |

**The two FUSB302Bs share address `0x22`, which is why they are on separate
buses** -- they cannot be distinguished on one. So "just use one bus" is not
available in hardware.

But one *controller* is: a single I2C master with a 2-bit bus select driving three
pin-sets. That is the multiplexed master #98 already asks for, and it replaces
three replicated controllers with one plus a mux.

### The interrupt lines can be OR-ed together

Each Type-C bus brings an `int` and a `fault` line, so six signals for two
devices. They do **not** need six PLIC sources: OR the `int` lines into one.

This is not only a logic saving, it follows from the mux. With a single
controller only one device can be talked to at a time, so per-device sources buy
nothing -- the handler has to serialise its register reads over the shared bus
regardless. And nothing is lost by merging them: the FUSB302B's interrupt
register has to be read to decode *and clear* the cause, so that read happens
either way.

**The trap, when this is built:** a shared line is level-sensitive, so the
handler must read and clear *every* asserted device before it returns, not just
the first one it finds. Missing one leaves the line asserted, the interrupt
re-fires immediately, and the result is a storm that presents as a hung CPU --
which on this project has repeatedly been mistaken for dead gateware.

Keep `fault` distinct from `int`. It means something different, and it is the one
worth noticing unambiguously rather than after a register read.

Not urgent. PD negotiation is not on the critical path; the value of the
interrupt is that a state change can be looked into when it happens instead of
polled.

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
existing `hello_soc.py` already reaches its console, so the pattern is proven in
this tree rather than proposed.

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

**FUSB302B and die temperature over the sideband.** Small, because the gateware
exists -- `ecp5-test/pins/fusb302_id.py` already reads both controllers. What is
missing is sideband *commands*, not blocks. Temperature needs the `DTR` primitive
instantiated, which it currently is not anywhere.

**Flash and HyperRAM benchmarking driven by the CPU.** The measurements that
motivated the RISC-V work. **Blocked** on the silent-SoC problem: both CPUs
enumerate and neither prints, cause not established
(`docs/moondancer/silent-soc-investigation.md`).

## What this does not resolve

**Whether it all fits.** Three USB stacks plus a CPU plus caches plus HyperRAM on
an LFE5U-12F is not obviously feasible, and nothing here has been synthesised
together. The area numbers to beat: best CPU config measured at 6342 LUT and
14 of 56 BRAM; the sideband test bitstream at 123920 bytes builds today with the
responder, flash and power monitor.

**The silent SoC.** Everything CPU-driven waits on it, and it is the one item in
this plan with no established cause rather than an unstarted task.
