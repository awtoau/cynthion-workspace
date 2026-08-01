# What we take from upstream, and what we have replaced

Which parts of LUNA, luna-soc and cynthion this workspace uses as-is, which it has diverged
from, and why. Written because the boundary was becoming emergent rather than decided —
things were being replaced one at a time, as each one broke, without anyone stating the
policy.

**The short version: we keep their USB and expect to take more of their gateware later.
We have diverged on four components and the pin map, each for a measured reason.**

**Standing direction on Great Scott Gadgets code:** reserve it for work that is genuinely
Cynthion-specific — the r1.4 pin map, the USB stack we already depend on, gateware unique
to this product. For anything general (an interrupt controller, a timer, a bus primitive)
look at upstream `amaranth-soc` first, then the wider Amaranth ecosystem, then write our
own. Writing our own is an acceptable outcome, not a last resort.

## The policy

**Use upstream where it works.** Their USB stack in particular is good, widely exercised,
and reimplementing it would be a large amount of work to arrive at something worse.

**Replace what blocks us, and say so here.** Every divergence below exists because
something was measurably wrong or measurably limiting, not because we preferred our own
version. Each has a recorded reason and, where the fault is upstream's, a reproducer.

**Vendor the board definition rather than inheriting a stack for it.** The r1.4 pin map is
340 lines of hardware wiring; taking it via `cynthion` pulls in `LUNAApolloPlatform`,
`LUNAPlatform` and a `luna-soc` fork pin.

## Used as-is, and we intend to keep using it

| what | from | why keep it |
|---|---|---|
| `USBDevice`, `USBSerialDevice` (CDC-ACM) | `luna.gateware.usb` | Measured at 195.4 Mbps loopback; CDC costs essentially nothing over raw bulk. This is the part of LUNA that is genuinely good. |
| `USBStreamInEndpoint`, `USBStreamOutEndpoint` | `luna.gateware.usb` | Same stack, same reasoning. |
| `StandardRequestHandler`, `ControlRequestHandler`, `SetupPacket` | `luna.gateware.usb.request` | USB protocol plumbing; no reason to touch it. |
| `IntegratedLogicAnalyzer` | `luna.gateware.debug.ila` | Works, fits in one DP16KD, found the CS bug. |
| `JTAGRegisterInterface` | `luna.gateware.interface.jtag` | Reliable, and the only debug path that does not depend on USB coming up. |
| `StreamSerializer` | `luna.gateware.stream` | Small, works. |
| `blockram.Peripheral` | `luna_soc.gateware.core` | Works, and its `writable=True` default is what makes runtime firmware loading possible. |
| `SPIPHYController`, `ECP5ConfigurationFlashInterface` | `luna_soc.gateware.core.spiflash` | The PHY and the `USRMCLK` handling are correct. The bugs were a layer above. |
| `SPIFlashMemoryMap` | `luna_soc.gateware.core.spiflash` | Verified byte-exact against `apollo flash-read`. |
| `amaranth-soc` | **upstream**, not luna_soc's vendored copy | See below. |

## Diverged, with reasons

### Clock generation — `VariableClockDomainGenerator`

Replaces `LunaECP5DomainGenerator`. In `repos/apollo/apollo_fpga/gateware/variable_clock.py`.

Upstream offers **60, 120 and 240 MHz only** — hardcoded PLL taps, so any speed ladder
steps in factors of two. That blocked two separate investigations: the HyperRAM ceiling is
recorded as "somewhere between 120 and 240" because nothing between could be built, and the
RISC-V clock sweep failed with `KeyError: 80`.

Nothing in the hardware requires those values. The PLL has four independent output
dividers; ours solves for `sync` **and** `usb` together so `usb` lands on exactly 60 MHz —
which `ecppll` does not do, because it optimises its primary output and lets the secondary
fall where it may. Asking it for 80/60 gives `usb` at 62.2 MHz, 3.7% out, and the ULPI PHY
does not enumerate.

See #111.

### SPI crossbar — `FairSPIControlPortCrossbar`

Replaces `luna_soc`'s `SPIControlPortCrossbar`. In `ecp5-test/riscv/vexii_flash.py`.

**Upstream bug.** It re-arbitrates only when the port holding the grant stops asserting
`cs` — but `cs` is a *hold* signal, not a request, and `SPIFlashMemoryMap` asserts it for
256 cycles after every burst. Any SoC that memory-maps flash *and* wants arbitrary commands
starves the controller permanently.

Reproducer: `scripts/riscv_flash_crossbar_sim.py`. Upstream grants at cycle 0 when the map
is idle and **never in 600 cycles** when it holds `cs`.

### SPI controller — `HoldableSPIController`

Replaces `luna_soc`'s `SPIController`. In `ecp5-test/riscv/vexii_flash.py`.

**Upstream bug, and an instructive one.** The CS register field is `csr.action.W` — a
one-cycle write pulse — while the code uses it as a latch:

    m.d.comb += cs.eq(self._cs.f.select.w_data | tx_fifo.r_rdy)

The comment above that line says *"Only disable chip select after the current TX FIFO is
emptied"*, so the **intent is exactly right**; the field type does not implement it. CS
collapses to `tx_fifo.r_rdy` and drops whenever the FIFO drains.

Found by ILA: chip select fragmented into four separate 8-bit windows during a JEDEC read,
deasserted for 81, 36 and 36 samples between them. The flash resets its command state on
each CS rise, so the response clocks ran with no command pending.

**Why nobody upstream hit it:** moondancer's `read_flash_uuid` writes all 13 command bytes
into the 16-deep FIFO in one loop before reading anything back, so `tx_fifo.r_rdy` never
goes low and the broken hold is never load-bearing. That workaround has a hard ceiling at
16 bytes — a 256-byte page program cannot fit — which predicts that flash *writes* from the
CPU have never worked in their design either. Consistent with nothing in moondancer writing
flash.

Ours uses a latching `csr.action.RW` hold register. Confirmed by ILA: CS is one unbroken
run.

### Interrupt controller — ours, written to the RISC-V PLIC spec

`ecp5-test/riscv/vexii_plic.py`. **No upstream code was used, from luna-soc or anywhere
else.** Three candidates were looked at first:

| candidate | why not |
|---|---|
| `luna_soc.gateware.cpu.ic.InterruptController` | Policy: GSG code is reserved for genuinely Cynthion-specific work (the board pin map, the USB stack). This is neither. It is also a pure concentrator for VexRiscv's non-standard in-CPU mask/pending CSRs, which VexiiRiscv does not have. |
| `amaranth_soc.csr.event.EventMonitor` | **Broken upstream.** It constructs and then fails to elaborate: `connect(m, self.bus, self._mux.bus)` is the wrong direction, so `bus.r_data` gets two drivers and Amaranth raises `DriverConflict`. It is also written against a `csr.Register`/`Element` API that no longer exists (`reg.element.w_stb`, `Field(FieldAction, ...)`), and `amaranth_soc.event.EventMap.add()` no longer takes the `name` its own caller passes. Nothing in the package imports it, which is presumably why nobody noticed. Reproducer: construct an `EventMonitor` with one source and call `rtlil.convert` on it. |
| VexiiRiscv's own PLIC | Tilelink only, and generated by the Scala toolchain rather than instantiable from Amaranth. |

So it is ours, and being ours is not a concession here — a PLIC is small, completely
specified by `riscv-plic-spec 1.0.0`, and the register discipline this workspace enforces
(no state-changing reads; nothing polled sharing a 32-bit word with something that does)
matters more than reuse. The standard map satisfies that discipline without adjustment:
the only side-effecting read is the claim at `0x200004`, alone in its word and two
megabytes from `pending`.

The reason it is a *standard* PLIC rather than the thirty-line pending/enable pair in
`ecp5-test/riscv/vexii_irq.py` is the same reason the console is a standard 16550: QEMU's
`-M virt` has one, so `firmware/cynthion-soc/src/plic.rs` is compiled unchanged for the
board and for `scripts/soc_test.py`. A bespoke controller would have made the gate stop
covering the interrupt path that ships. RTIC's RISC-V backend expects this map too.

**Worth noting rather than using:** luna-soc's `InterruptController` has one thing we did
copy the *shape* of — `add(peripheral, name=, number=)` and `interrupts()`, which its SVD
generator reads to name interrupts in the generated PAC. If a PAC is ever wanted here, that
is the interface to grow, and `vexii_irq.py` already has it.

### `amaranth_soc` — upstream, not the vendored copy

`luna_soc` vendors `amaranth_soc` and appends it to `sys.path` **only when
`import amaranth_soc` fails**. Nothing declared a dependency on the real package, so it
failed on every fresh environment and every `from amaranth_soc import ...` silently
resolved to a fork reporting version `unknown` — four commits behind upstream, including a
py3.14 annotation fix that had been hand-backported into it.

Fixed by declaring `amaranth-soc` explicitly in `cynthion`'s `pyproject.toml`, from git
(the PyPI package is a placeholder: version "0", no modules). The `try` then succeeds and
luna_soc's own peripherals bind to upstream too.

### Board platform — vendored

In progress. `CynthionPlatformRev1D4` is 206 lines of pin declarations plus a 134-line
base, but reaching it inherits `LUNAApolloPlatform` → `LUNAPlatform` and pins `luna-soc` to
the `awtoau/awto-luna-soc` fork. The target is a self-contained platform depending only on
`amaranth`, `amaranth.build` and `amaranth_boards.resources`.

## Expected to come back for more

**This is not an exit.** Vendoring the pin map and replacing three broken components is not
a decision to stop using upstream, and the list below is expected to grow in the *other*
direction as more of their work becomes useful.

Their gateware that we have not needed yet, and probably will:

| what | where | likely use |
|---|---|---|
| USB analyzer | `cynthion` gateware | the product's actual purpose -- capture and decode |
| `ApolloAdvertiser` | `apollo_fpga.gateware.advertiser` | claiming the CONTROL port, which AUX-only designs avoid today |
| Facedancer gateware | `cynthion/.../gateware/facedancer` | device emulation; also the SoC moondancer targets |
| `flash_bridge` | `apollo_fpga.gateware` | `flash --fast`, an FPGA-side flash writer |
| selftest bitstream | `cynthion.selftest` | already used by some scripts here |
| `luna_soc` peripherals | timer, uart, gpio, usb2 | needed for moondancer; none built yet |

Moondancer itself is the clearest case: it targets `riscv32imac` (which our CPU is), it
expects `blockram` at `0x00000000` and `spiflash` at `0x100b0000` (which we now have), and
it needs leds, gpio0, gpio1, timer0, timer1, spi0 and a USB peripheral — **all of which are
luna_soc peripherals we would take rather than write.**

So the rule is not "leave upstream behind". It is:

- **take what works**, and most of it does
- **replace what is measurably broken or limiting**, with the reason recorded here
- **do not inherit a stack to get one file** -- vendor the file

The divergences above are three components and a pin map. Everything else stays, and more
is expected to arrive.

## Not yet decided

**HyperRAM.** We use `HyperRAMInterface` and `HyperRAMPHY` as-is, and they work — 220.2
MB/s, 92.8% of theoretical. But there is **no Wishbone peripheral**, so a CPU cannot reach
it at all (#90), and the DQS path is unfinished (#92). Writing that adapter is unavoidable;
whether it wraps upstream's controller or replaces it is open, and worth deciding
deliberately rather than by accident.

Three bugs were found in *our own* use of that interface, not in it: `final_word` must be
held rather than pulsed, `perform_write`/`write_data` must be held for the whole transfer,
and `CHID` is a single register window so channel setup is not re-entrant. All three
produced plausible wrong answers rather than failures.

**`luna-soc` fork.** `cynthion` pins `luna-soc` to `awtoau/awto-luna-soc`. The fork existed
for an Amaranth API change and 40+ patched CSR classes; upstream is now **ahead**, so the
amaranth-soc reason is gone. Whether anything else still needs it has not been checked.

## Reporting upstream

Two clean defects with standalone reproducers — the crossbar starvation and the CS hold —
both in `greatscottgadgets/luna-soc`, both reachable only by memory-mapping flash *and*
using arbitrary commands. Cynthion's own SoC instantiates exactly that combination
(`with_controller=True, with_mmap=True`), so this is their code on their hardware.

Worth reporting as one issue rather than two: the CS bug is the interesting half, and the
explanation of why their firmware survives it is what makes the report actionable.

Per the workspace rules, upstream repos need three checks before anything is filed. Draft
locally first.
