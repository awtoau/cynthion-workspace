# Decisions, and the alternatives they were chosen over

Every technical choice on this project where a real alternative existed, in tables.
One file, so a decision is looked up rather than rediscovered.

**Scope.** This records *technical* comparisons. The *policy* on upstream code — what
Great Scott Gadgets code we reserve, and why — is [`upstream-boundary.md`](upstream-boundary.md).
Where a divergence from upstream has a measured reason, the reason is here and the
boundary doc links to it.

**Reading the tables.** **Bold** is what is in the tree today. "Forced" means the
alternative is unavailable in hardware or in the toolchain, not that it lost an argument.
Numbers are measured unless marked *unverified*.

---

## Index

| # | Decision | Chosen | Status |
|---|---|---|---|
| 1 | [CPU core](#1-cpu-vexriscv-vs-vexiiriscv) | VexiiRiscv | settled |
| 2 | [Caches](#2-cached-vs-cacheless) | cached | forced by atomics |
| 3 | [Clock generator](#3-clock-generation) | `VariableClockDomainGenerator` | settled (#111) |
| 4 | [Console peripheral](#4-console-peripheral) | NS16550A | settled |
| 5 | [FIFO depth](#5-uart-fifo-depth-8250--16550--16750) | fixed 16 | settled |
| 6 | [Buffering](#6-buffering-deep-fifo-vs-elastic-buffer) | elastic buffer at the transport | settled |
| 7 | [Interrupt controller](#7-interrupt-controller) | PLIC, written from spec | settled |
| 8 | [PLIC source granularity](#8-per-device-plic-sources-vs-or-ed) | per device | settled (#135) |
| 9 | [I2C topology](#9-i2c-three-controllers-vs-one-plus-a-mux) | one controller plus a mux | forced |
| 10 | [I2C register map](#10-i2c-register-map) | OpenCores rev 0.9 | settled |
| 11 | [SPI flash crossbar](#11-spi-flash-crossbar) | ours | upstream defect |
| 12 | [SPI flash CS hold](#12-spi-flash-chip-select-hold) | ours | upstream defect |
| 13 | [UART pad output enable](#13-uart-pad-output-enable) | ours | upstream defect |
| 14 | [`amaranth-soc` source](#14-amaranth-soc-upstream-vs-vendored) | upstream package | settled |
| 15 | [Firmware loading](#15-firmware-loading) | JTAG stream, and USB bulk | settled (#132/#114) |
| 16 | [Emulation](#16-emulation-and-simulation) | QEMU `-M virt` + targeted sims | settled |
| 17 | [Register access](#17-register-access-transcription-vs-generated-pac) | generated PAC, addresses only | settled |
| 18 | [Logging from handlers](#18-logging-from-an-interrupt-handler) | deferred ring | settled (#122/#124) |
| 19 | [Concurrency model](#19-rtic-or-preemption-without-it) | superloop today | **open** (#201/#115) |
| 20 | [Device ownership](#20-multi-transaction-device-protocols) | one owner, cached reads | settled (#123) |
| 21 | [16550: written vs vendored](#21-16550-written-from-the-spec-vs-a-vendored-core) | ours, spec-checked against OpenCores | settled (#128) |
| 22 | [What is resident at 0x0](#22-what-is-resident-at-0x0) | a 492-byte bootloader; the shell is an image | settled (#138) |
| 23 | [QSPI burst sequencer](#23-qspi-burst-sequencer) | an FSM with an arming cycle | fixed, simulated, unrun on the board (#89) |
| 24 | [What the sideband answers](#24-what-the-sideband-link-answers) | PING, STATUS, a byte each way; the rest removed | settled (#137) |
| 25 | [Advertisement on the sideband wire](#25-fpga_adv-advertisement-and-sideband-on-one-wire) | Apollo's UART-mode frame | settled (#137); **unverified on hardware** |

---

## CPU and clock

### 1. CPU: VexRiscv vs VexiiRiscv

| | VexRiscv | **VexiiRiscv** |
|---|---|---|
| Interrupts | non-standard `ExternalInterruptArrayPlugin`, 32-bit array, mask/pending in CPU CSRs 0xBC0/0xFC0 | standard RISC-V: one machine external wire |
| Uncached path | one bus, hardcoded "uncached iff address bit 31" | declarative per PMA region, on its own `iobus` |
| Wishbone masters | 2 | 3 (`FetchL1`, `LsuL1`, `LsuCacheless`) |
| Atomics | — | `--with-rva`, and the cached bridge carries them |
| Debug | — | `--debug-jtag-instruction`, off the ECP5's existing TAP |

Chosen for the standard privileged interface, the region-declared uncached path, and
the debug module. The interrupt consequence is decision 7.

**Area and timing, like for like** — `scripts/cpu_matrix.py`, log in `tmp/logs/cpu_matrix.log`
(2026-08-03), core plus block RAM only:

| variant | LUT4 | LUTRAM | COMB | FF | BRAM | Fmax | closes 60 MHz |
|---|---|---|---|---|---|---|---|
| VexRiscv `cynthion` | 4736 | 64 | 4736 | 1683 | 5 | 62.0 MHz | yes |
| VexRiscv `cynthion+jtag` | 5410 | 64 | 5410 | 1832 | 5 | 58.7 MHz | no |
| VexRiscv `imac+dcache` | 4564 | 40 | 4564 | 1712 | 7 | 53.7 MHz | no |
| VexRiscv `imc` | 3857 | 32 | 3857 | 1452 | 3 | 56.8 MHz | no |
| VexiiRiscv base | 3497 | 49 | 4182 | 1705 | 0 | 84.0 MHz | yes |
| VexiiRiscv +supervisor | 3825 | 49 | 4217 | 1757 | 0 | 87.8 MHz | yes |
| VexiiRiscv +rva | 3490 | 49 | 4208 | 1715 | 0 | 86.8 MHz | yes |
| VexiiRiscv +caches | 3870 | 100 | 4900 | 2374 | 6 | 91.5 MHz | yes |
| VexiiRiscv moondancer-like | 4126 | 100 | 5294 | 2460 | 6 | 84.4 MHz | yes |

Three columns rather than one, because one is misleading. **LUT4** is yosys' cell count.
**LUTRAM** is `TRELLIS_DPR16X4`, which is *not* in the LUT4 figure and is where a TLB
lands — an MMU read on LUT4 alone understates itself by half. **COMB** is nextpnr's
packed-slice count for the same design, which includes both, and is the only column in the
same unit as a whole-SoC utilisation report. **FF** is yosys' whole-design total; the top
module's own section excludes submodules and undercounts. The die has 24288 slices and 56
block RAMs.

Fmax comes from a four-pin timing harness — a shift chain into every input, registers and
a two-stage XOR tree out of every output — because a bare core has ~535 port bits against
the package's 197 pins and cannot be placed at all. The harness is validated by `soc-cpu`
below: 72.7 MHz in the harness against **72.40 MHz** for the whole SoC on the board's own
build, i.e. the core is the SoC's critical path and the harness finds the same one.

Caveats stated in the log itself: BRAM counts are low because a CPU with no firmware
never drives its bus and synthesis prunes the attached memory; CoreMark is not in this
matrix.

#### 1a. 64-bit and an MMU: what Linux would cost

Linux with Rust drivers forces rv64 with an MMU — stock distributions are rv64gc, glibc
has no upstream rv32 port, and Rust for Linux has no rv32 target — so the only question is
resources. One variable per row, from the `+caches` row above:

| variant | LUT4 | LUTRAM | COMB | FF | BRAM | Fmax | closes 60 MHz |
|---|---|---|---|---|---|---|---|
| rv32 +caches (above) | 3870 | 100 | 4900 | 2374 | 6 | 91.5 MHz | yes |
| rv32 +caches +supervisor, no MMU | 4123 | 100 | 5073 | 2426 | 6 | 90.0 MHz | yes |
| rv32 +caches +supervisor +MMU (sv32) | 4979 | 220 | 6626 | 2761 | 6 | 71.4 MHz | yes |
| rv64 +caches | 6182 | 148 | 7760 | 3835 | 10 | 81.2 MHz | yes |
| rv64 +caches +supervisor, no MMU | 6257 | 148 | 8135 | 3887 | 10 | 70.0 MHz | yes |
| rv64 +caches +supervisor +MMU (sv39) | 7529 | 300 | 10437 | 4390 | 10 | 76.8 MHz | yes |
| rv64 +MMU +rva | 8110 | 300 | 10948 | 4540 | 10 | 68.4 MHz | yes |
| rv64 +MMU +rva, 2-way caches | 9031 | 352 | 11758 | 4813 | **20** | 72.7 MHz | yes |
| rv64 +MMU +rva +rvfd | 17474 | 396 | **20927** | 8344 | 10 | **41.9 MHz** | **no** |
| `soc-cpu` — this SoC's own flags, rv32 | 5313 | 108 | 5999 | 3511 | 8 | 72.7 MHz | yes |
| `soc-cpu` +64 | 8613 | 164 | 10020 | 5105 | 12 | 75.8 MHz | yes |
| `soc-cpu` +64 +MMU | 9374 | 316 | 12109 | 5721 | 12 | 69.9 MHz | yes |

**The flags.** There is no `--with-mmu`. `--with-supervisor` is `addISA("s","u")`, and
`withMmu = checkISA("s") && !disableMmu` (`Param.scala:584,724`), so the MMU arrives as a
side effect of supervisor mode and the only way to name it separately is to ask for
supervisor and then take it away with `--without-mmu`. Two consequences: supervisor **is**
separable from the MMU (S-mode with `--without-mmu` — 253 LUT4 at rv32, 75 at rv64), the
MMU is **not** separable from supervisor, and `--without-mmu` on a core that never asked
for supervisor is a **no-op** — it is present on the base rows above and does nothing
there. `xlen` picks the scheme on its own: sv32 at 32, sv39 at 64 (`Param.scala:855`).
Because a flag that silently did nothing would read as a free MMU, `cpu_matrix.py` now
reads xlen, S-mode, MMU and FPU back out of the generated Verilog and prints what it
found rather than what was asked for.

**What binds.** Swapping this SoC's core for its 64-bit MMU equivalent is +6110 COMB and
**+4 block RAM** (`soc-cpu` → `soc-cpu+64+mmu`). Against the current whole-SoC build
(12903 COMB, 53%; 44 of 56 BRAM, 79%; 72.40 MHz) that is **19013 COMB, 78%** and **48 of
56 BRAM, 86%**. Both fit. The MMU itself is nearly free in block RAM — its TLB is
asynchronously read and lands in LUT RAM, +152 `DPR16X4` cells and 0 BRAM — and the four
block RAMs are the *width*, not the translation: a 64-bit L1 is twice as wide.

Block RAM binds first, and not on the core: **one more cache way costs ten block RAMs**
(10 → 20 at rv64). 4 KiB direct-mapped L1s are small for a kernel, and 8 KiB two-way ones
put the SoC at 44 − 8 + 20 = **56 of 56**, the entire die, before any of the RAM Linux
actually needs. Hardware floating point is the other wall: `+rvfd` alone is 20927 COMB,
86% of the die for the CPU by itself, and it misses 60 MHz at 41.9 MHz — so a stock
rv64gc userspace is out of reach, while a soft-float rv64imac kernel with Rust drivers is
not.

**The core fits and closes timing with margin; main memory is the half this table does not
measure.** 64 KiB of block RAM does not boot Linux, so it would have to be HyperRAM — a
bandwidth and latency question about the L1s in front of it, not an area one. The whole
chain is in [`linux-on-cynthion.md`](linux-on-cynthion.md), which corrects one reading
above: the block RAM wall is *this* SoC's, not the die's. A Linux-only build lands at 14 of
56, so 8 KiB two-way L1s cost 24 of 56 rather than the whole part.

**A trap in the tree**, superseded by the tables above:
[`luna_ecp5_fpga/riscv32_equivalence_and_variation_report_2026-07-22.md`](luna_ecp5_fpga/riscv32_equivalence_and_variation_report_2026-07-22.md)
headlines 12646 LUT4 for VexRiscv against 6876, which is **not like for like** — the
VexRiscv number includes the whole USB fabric and the VexiiRiscv one does not, so it
overstates VexRiscv by roughly a USB stack, and its two configurations differ in three
ways at once (caches, atomics, supervisor mode). That is why `cpu_matrix.py` exists. Its
benchmark rows are usable, as an apples-to-apples pair between *its own* two
configurations: CoreMark total ticks 6,133,969 vs 6,361,949 (+3.7%), DMIPS/MHz 0.74 vs
0.63.

### 2. Cached vs cacheless

**Forced.** The cacheless Wishbone bridge asserts `!up.p.withAmo`
(`LsuCachelessBridge.scala:203`), so it cannot be built with atomics. The firmware target
is `riscv32imac` and the A is atomics. The L1 bridge carries no such assertion, so the
cached configuration is the only one that runs the firmware.

Related trap: the generator flags dispatch per path (`Param.scala:932` cacheless, `972`
cached), so passing only the cacheless flags with caches enabled **silently** produces a
native core with no warning. Hence `--lsu-l1-wishbone` alongside `--lsu-wishbone` in
`ecp5-test/riscv/vexii_cpu.py`.

### 3. Clock generation

| | `LunaECP5DomainGenerator` | **`VariableClockDomainGenerator`** |
|---|---|---|
| Rates | 60 / 120 / 240 MHz only, hardcoded PLL taps | any `sync`, solved with `usb` |
| `usb` accuracy | exact at its three rates | exact 60 MHz, by construction |
| Blocked | HyperRAM ceiling recorded as "somewhere between 120 and 240"; RISC-V sweep died with `KeyError: 80` | — |

Plain `ecppll` is not a substitute: it solves for one clock. Asking it for 80/60 gives
`usb` at 62.222 MHz, **3.70% out**, and the ULPI PHY does not enumerate.

Across 60–130 MHz in 2 MHz steps, **only 60, 100 and 120 land on an exact 60 MHz `usb`**
— [`soc-clocking.md`](soc-clocking.md) §1.

**Hardware ceiling ladder** (same doc):

| requested `sync` | nextpnr achieved | on the board |
|---|---|---|
| 60 | 72.6 MHz | PASS (`369d0368`) |
| 80 | 76.3 MHz | PASS, 1.33x on the CPU clock |
| 90 | 86.1 MHz | does not enumerate (`usb` 63.000 MHz) |
| 100 | 92.0 MHz | enumerates, output corrupted |
| 110 | — | enumerates, output corrupted |

Traps recorded with it: an early version doubled every rate, because the feedback divider
was copied from LUNA without adjusting the VCO; and `SidebandDebug` derives its baud from
`clk_freq_hz`, so a design that raises `sync` and leaves the default gets a **dead** debug
link rather than a slow one (a UART tolerates about 2%; the SoC's derived divisor is
0.03% out).

Diamond was tried as an alternative synthesis path for the higher rates and abandoned:
LSE ran **21 min 23 s at 98–99% CPU without emitting a netlist**, against roughly 20 s for
the whole yosys + nextpnr flow on the same RTL.

---

## Peripherals

### 4. Console peripheral

| | bespoke 2-register console | **NS16550A** |
|---|---|---|
| Layout | four byte registers packed into one 32-bit word | RBR at +0, LSR at +5 — different words |
| Poll hazard | reading `rx_data` at +2 popped the RX FIFO, one byte from `rx_valid` at +3 | structurally impossible |
| Driver | ours, and a second one for QEMU | one `src/uart.rs` for board and QEMU |
| Block RAM | — | 44 → 43 DP16KD **while gaining a second UART** |

The hazard is not theoretical. Anything that widens, prefetches, speculates, replays or
retries a read — a cache line fill, a bus bridge sweeping all four byte lanes, a debugger
peeking at memory — consumed a received byte that no software asked for. **On this board a
build that never called `Console::get()` printed normally while one that polled it went
silent.** The same shape had already cost a day on luna_soc's `SPIController`, where
reading `data` pops its RX FIFO.

The second reason is the test gate: `-M virt` presents an NS16550A, so
`firmware/cynthion-soc/src/uart.rs` and `firmware/cynthion-soc/src/plic.rs` compile
unchanged and `scripts/soc_test.py` exercises the code that ships rather than a lookalike.
`target_soc.rs` and `target_qemu.rs` collapsed into one `target.rs`.

**The register semantics are the standard's, read-to-clear included (#128).** The
deviation that was weighed — IIR at +2 shares a 32-bit word with RBR at +0, so a
state-changing read there could strobe RBR if the CPU widened a byte access, the shape of
the bug this peripheral replaced — does not survive measurement:

| | measured |
|---|---|
| Does this CPU widen a byte read? | **no.** `LsuCachelessWishbonePlugin` drives a single-byte `sel` (`VexiiRiscv.v:7499-7515`) |
| Does the bridge strobe the other lanes? | **no.** `amaranth_soc.csr.wishbone` asserts `r_stb` per lane, `sel_index & ~we` |
| Could a cache line fill reach it? | **no.** the console is in a `main=0` PMA region |
| What protects the poll loop? | **the layout.** LSR at +5 is a different 32-bit word from RBR at +0, which holds whatever IIR does |

Deviating costs the property the standard map was chosen for: with IIR clearing nothing and
THRE level-derived, a driver that sets ETBEI, takes the interrupt, reads IIR and returns —
what the standard says to do, and what every 8250 driver does — never clears the transmit
indication and gets an interrupt storm. Fighting a common, well-understood idiom buys a
guarantee the layout already gives.

Two conformance points a stock driver depends on, and neither is obvious from the register
table alone:

  * **LSR.THRE means "the transmit FIFO is empty", not "it has room".** Linux's 8250 takes
    the standard at its word: `serial8250_tx_chars` writes `up->tx_loadsz` — 16 — bytes
    after seeing THRE once, which against the weaker meaning is fifteen bytes into a FIFO
    with room for one. OpenCores agrees: `assign lsr5 = (tf_count==5'b0 && thre_set_en)`.
  * **IIR reads `0b0001` at rest.** The standard's table has exactly one entry for idle; a
    value that keeps the transmit id in bits 3:1 while bit 0 says "no interrupt pending"
    is not in it.

LSR reports its error bits — OE, FE and the bit 7 summary, cleared by the read that
reports them. The peripheral cannot see an overrun itself: its `sink` backpressures, so a
full FIFO there is a stall and not a loss, and the transport reports a destroyed byte on a
pulse. Only the Apollo port can produce one — a line with no flow control — because the USB
CDC endpoint NAKs while its buffer is full and the host retries.

**The acceptance test — could a stock 8250 driver drive this unmodified?** Walked against
`8250_port.c` in
[`chips/ns16550a-console-uart.md`](chips/ns16550a-console-uart.md): yes, including
`autoconfig`'s presence test and the TXEN-bug test, with one gap — `autoconfig_irq`, which
needs the *data* half of MCR loopback to discover an unknown interrupt line. A driver told
its interrupt by a devicetree never runs it. Two of the fixes above (THRE, and MCR.LOOP
routing MCR into MSR) came out of that walk rather than out of the issue.

### 5. UART FIFO depth: 8250 / 16550 / 16750

| | 8250 | **16550A** | 16650 / 16750 |
|---|---|---|---|
| FIFO | none | 16 bytes, fixed | 32 / 64, discoverable |
| Driver assumption | — | every driver assumes 16 on seeing 16550A in IIR | needs a depth register, and nothing agrees about them |
| ECP5 mapping | — | 16 × 8 bits → distributed LUT RAM (TRELLIS_DPR16X4) | 1024 × 8 → a DP16KD |

Depth is a constant, not a parameter: making it adjustable means firmware has to discover
it. An 8250 (no FIFO) peripheral was never weighed — 8250 appears in this tree only as the
name of the Linux driver.

### 6. Buffering: deep FIFO vs elastic buffer

| | deep FIFO inside the UART | **`StreamBuffer` at the transport** |
|---|---|---|
| Sized for | nothing in particular | the transport's stall, packet size and clock |
| Failure seen | a 1024-byte FIFO justified as "two 512-byte USB packets" outlived `serial.tx.last.eq(1)` (one byte per packet) and went on costing a DP16KD | — |
| Cost | one block RAM per console | distributed LUT RAM |

Two correct decisions in one module silently invalidated each other, and nothing pointed
at the contradiction because it sat inside a module about register layout. Buffering now
lives where the transport is chosen. Overflow is dropped at the *producer* by backpressure,
so nothing is lost as long as the producer honours `ready`.

**CDC trap recorded with it:** a `SyncFIFOBuffered` between `sync` at 80 MHz and `usb` at
60 worked perfectly while both were 60, then produced a stream with correct counter values
and **dropped characters** — `tic 00000`, `tck 000001`. Both domains are explicit
parameters as a result.

### 9. I2C: three controllers vs one plus a mux

**Forced.** r1.4 has three physically separate I2C buses because **both FUSB302Bs answer
address 0x22** and cannot be distinguished on one bus.

| | three replicated controllers | **one controller, three pin-sets, 2-bit select** |
|---|---|---|
| Prior art here | `ecp5-test/pins/fusb302_id.py` builds one per bus | `ecp5-test/riscv/i2c_master.py` + `i2c_mux.py` |
| CPU view | three peripherals to tell apart | one peripheral it addresses |
| Area | three bit engines | one |

First run on silicon: commit `bd7867b`. Verified — 0x22 answers on `target_type_c` and on
`aux_type_c`, 0x10 on `power_monitor`, both FUSB302Bs read device id 0x91, matching what
was read over JTAG. A physical attach or detach has **not** been exercised.

`ecp5-test/i2c/multiplexed.py` is the earlier design of the same shape: simulation only,
never on silicon, kept for its sim tests.

Design consequences worth keeping in view:

  * The select is written before every transaction and never remembered. A stale select
    does not produce an error — it produces **a plausible answer from the wrong chip**.
  * The select is held until the controller is idle. Switching pin-sets between a START
    and its STOP leaves one bus half-driven and puts an edge on another that every device
    on it reads as a START.
  * The select **resets to the power monitor**. Zero would have been `target_type_c`, and
    a bitstream whose firmware never ran would have reported no rails with nothing saying
    why.
  * Unselected buses are *driven* idle. These pins are `PULLMODE="NONE"`, so undriven
    means floating, and a floating SDA reads as a START.

**Rejected sibling idea:** presenting the LEDs as a fake I2C device, for uniformity.
Wrapping one combinational assignment in a serial protocol adds a state machine, a
byte-time of latency and an error path. Uniformity is worth paying for at a *bus*
boundary, not inside the chip.

### 10. I2C register map

| candidate | verdict |
|---|---|
| `amaranth-soc` | no I2C peripheral at all (`gpio`, `event`, `memory`, bus fabric, nothing else) |
| `amaranth-stdio` | it is `serial.py` and nothing else |
| luna_soc peripherals | reserved by policy, and there is no I2C among them |
| **OpenCores "I2C-Master Core" rev 0.9** | public, has a Linux driver (`i2c-ocores`), and **no read with a side effect anywhere in it** — status is read from SR, the interrupt flag is cleared by *writing* IACK |

Not implemented, with reason: clock stretching. `power_monitor.scl` is `Pins("D7", dir="o")`
— an output with no `oe`, driven push-pull — so a slave that stretched the clock would be
shorting against this driver. The PAC1954 does not stretch (DS20006539B).

### 11. SPI flash crossbar

**Upstream defect.** luna_soc's `SPIControlPortCrossbar` gates its round-robin on
`grant_update.eq(~rr.valid | ~rr.requests[i])` — it re-evaluates only when the incumbent
stops asking. `cs` is a *hold*, not a request, and `SPIFlashMemoryMap` holds it for
`MMAP_DEFAULT_TIMEOUT` (256 cycles) after every burst, which is what lets a sequential
read skip the command, address and dummy phases.

| | upstream | **`FairSPIControlPortCrossbar`** |
|---|---|---|
| Re-arbitrates | when the incumbent yields | whenever the PHY is between transfers |
| Measured | controller **not granted in 600 cycles** with the map holding `cs`; granted at cycle 0 with it idle | grant arrives |
| Symptom | memory-mapped reads perfect, every controller command returns zeros — reads as a broken controller | — |

Reproducer: `scripts/riscv_flash_crossbar_sim.py`. Proven in simulation only, which is why
`grant` is brought out to hardware instrumentation.

### 12. SPI flash chip select hold

**Upstream defect, and an instructive one.** luna_soc's `SPIController` declares the CS
field as `csr.action.W` — a one-cycle write pulse — and uses it as a latch. The upstream
comment states the correct intent ("only disable chip select after the current TX FIFO is
emptied"); the field type does not implement it, so CS collapses to `tx_fifo.r_rdy`.

| | upstream | **`HoldableSPIController`** |
|---|---|---|
| CS field | `csr.action.W` (pulse) | `csr.action.RW`, plus a `hold` register at its own offset |
| Measured (ILA) | CS fragmented into **four separate 8-bit windows** during a JEDEC read, deasserted for **81, 36 and 36 samples** between them; `dq_i[1]` reads zero for all 32 bits | CS is one unbroken run |

**Software cannot work around it.** Queueing every transfer before draining any was tried:
the CPU issues CSR writes far more slowly than the PHY drains the FIFO, so the FIFO empties
mid-command regardless of ordering. The 81-sample gap in the capture is precisely the CPU
writing registers while the FIFO sits empty.

Why nobody upstream hit it: moondancer's `read_flash_uuid` writes all 13 command bytes into
the 16-deep FIFO before reading anything back, so `r_rdy` never drops. That workaround has
a hard ceiling at 16 bytes — a 256-byte page program cannot fit — which predicts that CPU
flash *writes* have never worked in their design either.

Kept from upstream deliberately: `SPIPHYController` and `ECP5ConfigurationFlashInterface`
(the PHY and the `USRMCLK` handling are correct; the bugs were a layer above), and
`SPIFlashMemoryMap` (verified byte-exact against `apollo flash-read`).

**Read mode, a related choice in the same file.** Upstream hardcodes `0xeb` Quad I/O Fast
Read in the body of `elaborate`. `ModalSPIFlashMemoryMap` moves the four constants into
`__init__`:

| mode | opcode | lanes | dummy | bring-up value |
|---|---|---|---|---|
| SINGLE | 0x03 | 1 throughout | 0 bits | one output pin, one input pin — it works or the wiring is wrong |
| QUAD | 0xeb | cmd 1, addr/dummy/data 4 | 24 bits, value `0xff0000` | fast, but a wiring fault, a sample-timing fault and a mode fault all return wrong bytes |

`0xff0000` is not arbitrary: `0xeb` sends mode bits M7–M0 straight after the address, and
`0xff` is not `0xax`, so the flash does not enter Continuous Read. `0xa0` would leave the
chip expecting an address where the controller sends a command — every read after the
first is garbage while the first looks perfect.

PHY divisor at 80 MHz `sync`: d=0 → 40.0 MHz, d=1 → 20.0, d=2 → 13.3. The ECP5 `MCLK` pin
is characterised to 62 MHz (FPGA-TN-02039); faster than 40 has been measured to work on
this board, which is not what a default should assume.

### 23. QSPI burst sequencer

**Our own defect, and the same class as 11 and 12: a signal whose temporal semantics do
not match how it is used.** The sequencer in `ecp5-test/qspi/qspi_gateware.py` selected the
read length with `Mux(bursting, burst_len, READ_BYTES)` and set `bursting` with `m.d.sync`
on the same cycle it strobed `start`, so the first read of a burst was armed while `length`
still read 32768.

`QuadFlashReader` latches `length` into `bytes_left` at the strobe and requests against it,
but decides it has finished by comparing its received count against the **live** `length`.
Latched 32768, live 4 by the next cycle: it requested one byte more than it consumed. The
surplus stayed in the controller's pipeline, the deframer held `frames.ready` low, the
IOStreamer's skid buffer filled, and `i_stream.ready` went low and stayed there. The next
read's HEADER waited on it for ever.

| | committed | **`BurstSequencer`** |
|---|---|---|
| `length`, `address` | combinational off `bursting`, which moves one cycle after `start` | registers, written only in IDLE, held across the strobe |
| arming | `start` combinational off the trigger and off the `done` edge | an ARM state, one cycle, reader provably in IDLE |
| trigger | `run & ~busy`, a one-cycle sample | latched into `pending` |
| `busy` | the reader's | the run's, covering a latched trigger |
| result | 1 of 4 reads, `i_stream.ready` low for ever | 4 of 4 |

**Not the handshake, which is the diagnosis this invites.** The reader sets `done` and
moves to IDLE on the same clock edge, so a strobe on the rising edge of `done` *is* taken;
`scripts/qspi_burst_sim.py` section 1 shows that handshake working while the design wedges
anyway. The fault is the live `length`.

**Pipeline depth is why simulation had not caught it.** Glasgow's `IOStreamer` reads its
buffer latency off the platform: 2 with no platform, 4 for a `LatticePlatform`. At 2 the
surplus request is never issued and the burst completes. The simulation therefore runs
against a platform object that reports itself as Lattice, with `io.Buffer`'s simulation
model restored for the simulation ports, and section 7 asserts both outcomes so the trap
stays documented.

**Deleting it was the other option, and the reason given for it does not hold.** #89 argued
the sequencer should go because a JTAG register read takes ~35 ms against ~1 us for a
4-byte flash read. That is true of timing one read from the host, and irrelevant here: the
sequencer counts sync cycles for a whole run in the FPGA and the host reads the total once.
The 35 ms is paid per run, not per read. It is the in-FPGA measurement #89 asks for, one
soft CPU cheaper.

### 13. UART pad output enable

**Upstream defect, third of three, same shape: a hold expressed as a ready.** luna_soc's
UARTProvider drives the pad's output enable from `~phy.tx.rdy`. `rdy` is combinational on
the transmitter's IDLE state and the FSM enters IDLE on the same cycle it shifts the stop
bit out.

| | `oe = ~tx.rdy` | **`SerialLine`** |
|---|---|---|
| Measured, divisor 8 | data bits end at cycle 79; `o` and `rdy` both rise at 80; the stop bit occupies 80..87 — **driven for none of it** | `oe` held `(1 + frame_bits) * divisor` cycles, reload wins over decrement |
| Symptom | the line RC-charges through a tens-of-kΩ pull-up from a hard 0 (every ASCII character has bit 7 low) and usually arrives in time — intermittent console corruption, not a dead link | — |

Reproducer `scripts/soc_serial_sim.py`; fixed in `52c607a`, issue #113. `SerialLine` also
adds the receive synchroniser, the framing-error drop and the idle qualifier that
`AsyncSerial` leaves to the instantiator — see its module docstring.

### 24. What the sideband link answers

**The shipping link is `ecp5-test/sideband_link.py`, and it has three commands:** `PING`,
`STATUS`, and `0x80`–`0xFF` to deliver a byte to the CPU. That is a heartbeat and a byte
each way, which is the whole of what the shipping bitstream needs the wire for. The
responder with the full set stays in `apollo_fpga.gateware.sideband`, instantiated by
`ecp5-test/sideband/sideband_gateware.py`.

**The split is by what else can answer.** A responder that reads hardware from the fabric —
`POWER` from the PAC1954, `DEVICES` from the flash JEDEC ID and a HyperRAM presence bit,
`LED` for the board display — needs no CPU, which is what a board that will not boot far
enough to have a console needs, and which a booting design has better sources for:

| what the sideband can answer with no CPU | what answers it once the SoC runs |
|---|---|
| PAC1954 power | `power`, over I2C, all four rails |
| flash JEDEC ID, HyperRAM presence | `board`, from the caches rather than the bus |
| image, CPU, gateware identity | `info` and `selftest`, from inside the process |

Two consoles on independent ports (#128) and the SoC reading its own hardware (#126, #127)
leave the sideband a second, lower-fidelity path to facts already available — so the
shipping bitstream drops them and the test bitstream keeps them.

**`POWER` and `DEVICES` are removed, not stubbed.** The alternative was to keep answering
them with a correct frame length, a valid CRC and zeros in the payload, so the host side
would not have to change:

| | zero-filled | **removed** |
|---|---|---|
| A `POWER` query returns | a well-formed measurement of nothing | unknown command, OK bit clear |
| The reader must know | which fields are real in which bitstream | nothing; the reply says |
| Host decoder | keeps opcodes no gateware implements | matches the gateware |

Zero-filling is a read-old-format fallback wearing a protocol's clothes: a design must not
answer as though it might have a capability it does not have. `PING`'s version byte is 2
rather than 1 so the two maps are told apart at runtime rather than inferred from a command
that failed. `scripts/sideband_decoder.py` carries the shipping map alone; the test
bitstream's extra commands are in `ecp5-test/sideband/test_protocol.py`, beside the
bitstream that answers them.

**Apollo needs no change.** Its firmware never knew these opcodes — the host supplies the
command byte and the expected length through vendor request `0xC3`, and
`fpga_adv_command()` shifts whatever it is given
(`repos/apollo/firmware/src/boards/cynthion_d11/fpga_adv.c:418`). The only opcode-derived
constant is `ADV_RESPONSE_MAX 18` at line 143, sized for `POWER`; the shipping link's
longest reply is 4 bytes, so it is now larger than needed and still correct. Both maps
in full: [`chips/cynone-sideband.md`](chips/cynone-sideband.md#4-commands).

**Measured, `scripts/sideband_cost.py`:**

| | logic | FF |
|---|---|---|
| `SidebandResponder`, as the SoC drove it | 245 | 109 |
| `SidebandLink` | 226 | 96 |
| **saved** | **19** | **13** |
| sourcing the removed fields would have cost | +299 | +0 |

**Nineteen cells, 0.16% of an LFE5U-12F.** The change does not stand on the saving; it
stands on the reply, which says what the design is rather than answering for what it is
not. The 299 is what those commands would cost if anything drove them — the SoC never did,
so the fields were constant-folded and that cost was never being paid. Issue #137.

### 25. FPGA_ADV: advertisement and sideband on one wire

**Upstream the pin's job is port takeover.** `ApolloAdvertiser` drives FPGA_ADV as a 50 Hz
square wave, and Apollo keeps the CONTROL port switched to the FPGA only while that
continues. Every bitstream here is AUX-only, so the pin carries the sideband instead —
which leaves the SoC **no way to ask for CONTROL** unless both jobs share the wire.

A square wave and a UART cannot share the wire: the square wave is low for half of every
20 ms and no byte survives it.

| approach | verdict |
|---|---|
| Square wave, sideband dropped | forfeits the link that works when USB does not |
| Square wave and sideband time-sliced | Apollo's EIC mode counts *edges*, and sideband traffic is edges — a poll reads as a port request |
| **The frame `C1 14 01 A5` on the same UART** | Apollo's `FPGA_ADV_MODE_UART` already defines exactly this, and already routes bytes to the pattern matcher only when no command is in flight |
| "The link is alive" as the advertisement | Apollo grants on the frame and on nothing else; a reply is deliberately never offered to the matcher |

The third row is not a new protocol — it is the one Apollo's firmware has implemented since
the sideband landed, and which **no gateware ever emitted**. `ecp5-test/sideband_advertise.py`
emits it. The decision was to find the mechanism rather than invent one.

**What it costs.** The link's rule was "the FPGA never transmits unasked", which made it
collision-free by construction. An advertisement breaks that rule, at a bounded price:
0.17% duty, open-drain overlap, CRC-and-retry, three frames per timeout, and a
20-bit-period idle guard. The mechanism and its numbers are in
[`chips/cynone-sideband.md`](chips/cynone-sideband.md#8-the-second-job-the-control-port-request); what the price
does *not* cover is a saturated poll, which starves the guard and loses the port —
[`chips/cynone-sideband.md` §9](chips/cynone-sideband.md#9-how-the-two-jobs-interfere) and #184.

Measured cost: **+124 logic cells and +82 flip-flops**, against a shipping sideband of 350
logic and 178 FF — 2.9% of an LFE5U-12F. The port request is the larger part of this
issue's net change by some margin: decision 24 removes 19 cells, this adds 124.

**Polarity is inverted from upstream.** `ApolloAdvertiser` advertises from configuration
and `ApolloAdvertiserRequestHandler` supplies `stop`; here the bit resets clear and
firmware sets it. A bitstream that seized CONTROL the moment it configured would take the
port away from Apollo's own debug interface, which is the path used to recover a board
that will not boot. The `stop` signal is what says the handover was always meant to be
software-controlled from the FPGA side; this makes the *request* software-controlled too.

Sideband control register bit 5, `sideband::ADVERTISE`. Issue #137, and it closes the
`ApolloAdvertiser` row in [`upstream-boundary.md`](upstream-boundary.md).

**Not verified on hardware.** No bitstream has been built or flashed with this, and Apollo
has never been put into `FPGA_ADV_MODE_UART` from the host — see
[Unverified](#unverified).

---

## Interrupts

### 7. Interrupt controller

| candidate | verdict |
|---|---|
| luna_soc `InterruptController` | reserved by policy, and it is a pure concentrator for VexRiscv's in-CPU mask/pending CSRs, which VexiiRiscv does not have |
| `amaranth_soc.csr.event.EventMonitor` | **broken upstream.** Constructs, then fails to elaborate: `connect(m, self.bus, self._mux.bus)` is the wrong direction, so `bus.r_data` gets two drivers and Amaranth raises `DriverConflict`. Also written against a `csr.Register`/`Element` API that no longer exists. Nothing in the package imports it, which is presumably why nobody noticed |
| VexiiRiscv's own PLIC | Tilelink only, and generated by the Scala toolchain rather than instantiable from Amaranth |
| a 30-line pending/enable pair (`ecp5-test/riscv/vexii_irq.py`) | works, and was nearly what shipped — see below |
| **written from `riscv-plic-spec` 1.0.0** (`ecp5-test/riscv/vexii_plic.py`) | chosen |

**Why the full spec PLIC rather than the 30-line pair.** QEMU's `-M virt` has a PLIC, at
0x0c000000, with the 16550 on source 10. A bespoke controller would mean a different one
in the QEMU build, and the test gate would stop covering the interrupt path that ships —
the same property that made the console a standard 16550. Second reason: every generic
Rust PLIC driver (`riscv-peripheral`) expects this register map.

**RTIC is not one of the reasons.** It has no PLIC backend in any released version and
never reads a claim register: its generic RISC-V backends dispatch out of `riscv-slic`, a
*software* controller, from the machine software interrupt —
[`rtic-adoption.md`](rtic-adoption.md) and decision 19.

It also satisfies the register discipline without adjustment: the only side-effecting read
is the claim at 0x200004, alone in its 32-bit word, and `pending` — the register a poll
loop reads — is at 0x001000, two megabytes away.

**What it cost, and what got it back.** Adding the PLIC and the second UART
(commit `26ef424`) dropped Fmax on `sync` from 76.8 MHz to 60.8–67.5 MHz against a 60 MHz
constraint — as little as 1.3% margin. The critical path was **16.45 ns of which 13.64 ns
was wire**, and no logic optimisation shortens a path that is 83% routing. A flip-flop in
the Wishbone response path (`18c1fa5`) is what got it back:

| | before | after |
|---|---|---|
| Fmax on `sync` (4 builds) | 62.4–71.7 MHz | 79.2–80.7 MHz |
| spread | 9.3 MHz | 1.4 MHz |
| single transfer | 1 cycle | 2 cycles |
| burst beat | 2 cycles | 3 cycles |
| cache line refill | — | 1.5x as long |
| utilisation | — | +420 LUT, +33 FF; block RAM unchanged at 42 of 56 |

Verified: 26 PLIC checks, 16 QEMU checks, 9 bus checks; 24 interrupts taken with 0 stalls.
`scripts/soc_plic_sim.py` asserts on priority explicitly, because a sim that does not will
pass while the priority registers are stored, read back and ignored — the last source
examined wins and nothing says so.

### 8. Per-device PLIC sources vs OR-ed

**Per device.** Each FUSB302B `int` line has its own source — TARGET on 4, AUX on 5
(#135).

| | **per device** | OR-ed |
|---|---|---|
| Which device asserted | the claim says so | read the mux's `LINES` over the CSR bus |
| Clearing obligation | one device behind the source; nothing to miss | **must clear *every* asserting device before re-enabling**, or the level re-fires forever |
| A device mid-deferral | masks itself | masks both — the other port's event is invisible until the first is serviced |
| Diagnostics | `irq` counts TARGET and AUX separately | one total |
| Cost | one more priority register, pending bit and comparator | — |

**OR-ing conserves nothing scarce:** the PLIC allows 31 sources and this design uses 5.

**Serialised servicing is not an argument for OR-ing either.** Only one device can be
addressed at a time — the I2C controller is muxed,
[decision 9](#9-i2c-three-controllers-vs-one-plus-a-mux), forced by hardware — but that is
a statement about the bus, not about which source raised the interrupt. Applying a bus
constraint to the interrupt map is the same error as sizing the console FIFO for USB
packets ([decision 5](#5-uart-fifo-depth-8250--16550--16750)): an optimisation by analogy,
where the constraint does not reach.

**What per-device does not buy: concurrency.** Servicing still serialises. It buys
*knowing which*, and it deletes a correctness obligation rather than documenting one.

**The handler does not clear the controller it claimed.** Clearing one means
reading three read-to-clear registers over I2C — about a millisecond at 80 kHz — on the
single controller the power monitor's poll is also using: a long spin in interrupt context
*and* a second master on a peripheral with no lock. It masks the source it claimed and
records which port through the ring (decision 18); normal context clears that device and
re-enables that source. The mask is per device, so a TARGET awaiting its I2C clear does not
blind AUX.

`fault` gets **no** source. Two reasons, the second binding: it means something different
from `int` and is worth telling apart without a register read; and **nothing in the
firmware can clear it** — it drops when the device's fault does. An interrupt on an
uncleanable level must stay masked until something notices the level has gone, which is the
50 ms poll, so it would add a handler and keep the poll. `int` is clearable, which is why
masking terminates there.

Asserted in `scripts/soc_plic_sim.py` (the mux wired to a PLIC: the claim names the device,
and the quiet device's source is never involved) and `scripts/soc_board_sim.py` (one line
dropping leaves the other asserted).

**One source is wired but not enabled:** I2C transfer completion (source 3). The gateware
raises it; `CTR.IEN` resets to zero and the firmware polls `SR.TIP` instead. A shell
command that reads a register is synchronous by construction and has nothing else to do
while it waits, so an interrupt would buy it nothing and cost a handler that has to be
right. New sources are added at the end so nothing above renumbers.

---

## Firmware

### 15. Firmware loading

Baseline: firmware is block RAM init, so it lives inside the bitstream and changing one
byte costs a full resynthesis — **about 60 s** for a design whose logic is bit-for-bit
identical.

| | `ecpbram` placeholder | JTAG registers | **JTAG stream** | **USB bulk + HyperRAM** |
|---|---|---|---|---|
| Mechanism | rewrite BRAM init in a built bitstream | one register write per 16-bit word | one DR shift carries the whole image | the CPU writes HyperRAM itself |
| Time for 32 KiB | ~1 s | **28 ms per 16-bit word** → ~7.6 min | **85 ms** | a few seconds of serial transfer |
| Needs | a matching random placeholder | a debug session | a configured FPGA and nothing else | a running CPU, console and USB |
| Works with a wedged console | yes | yes | **yes** | no |
| Verdict | works, rejected as a coupling | **slower than the 60 s rebuild it existed to replace** | chosen for recovery | chosen for iteration |

Two of these are kept. The USB path is what a working board uses; the JTAG stream is
what a board whose console is not answering uses, and it is the one that can hold the
CPU in reset while it works — the USB path needs a running shell to receive its own
replacement.

**The register and stream figures differ by 5400x on the same wire.** Both are JTAG
through the same SAMD11 at the same 12 MHz TCK; `scripts/soc_jtag_stage.py --benchmark`
measures them one after the other in a single session, so nothing about the host differs
between them. Measured on r1.4:

| payload | time | rate | vs the register interface |
|---|---|---|---|
| one 16-bit word, register interface | 28.0 ms | 71 B/s | 1x |
| 1 KiB, streamed | 9.0 ms | 111 KiB/s | 1593x |
| 16 KiB, streamed | 46.0 ms | 348 KiB/s | 4986x |
| 32 KiB, streamed | 85.0 ms | 377 KiB/s | 5400x |
| 374 KiB bitstream, `apollo configure` | 1273 ms | 294 KiB/s | — |

The streamed image is **faster than Apollo's own bitstream path on the same board in the
same session**, which is the strongest available statement that the transport is not
what was slow. A register interface is a control-plane mechanism; the ~28 ms is two
IR+DR shift pairs plus `run_test`, and every one of them is a USB control transfer at
~13 ms. Bulk transport does not become fast by being asked for two bytes at a time.

`ecpbram` locates the old contents **by value**, and a real firmware image is ~87% zeroes,
which also fill every unused BRAM tile on the die — so it refuses with "Conflicting from
pattern" unless the design was synthesised against a known *random* placeholder:

    firmware: 8268 bytes into 16384 words (65536 bytes of block RAM)
    ecpbram failed:
    Conflicting from pattern for bit slice from_hexfile[3071:2816][0]!

HyperRAM rather than straight into the target slot because the next step is a reboot, and
a reboot is what block RAM does not survive intact — the shell doing the receiving is
executing from it. The bootloader's CPU-side read port moves one 16-bit word at a time
with no FIFO and no side-effecting read: roughly **8 ms for a 32 KiB image at 60 MHz**,
against the ~60 s it replaces. Bursting is available later.

Both staging paths land in the same layout — magic `CYNB`, length, CRC32, image from
word 16 — so `try_boot` cannot tell them apart and needed no change to accept a
JTAG-staged image.

**What the JTAG stream cost.** 234 LUT, 259 FF, 10 LUT-RAM slices; block RAM unchanged
at 42 of 56, because the FIFO is 32 entries and fits in distributed RAM. `sync` Fmax
fell from a median of 83.88 MHz over 6 runs to **76.75 over 12**, min 78.36 → 70.47,
max 86.71 → 82.30. Every run still closes the 60 MHz constraint with 17% or more to
spare, but the minimum now sits on the 70 MHz line rather than comfortably above it.
The critical path stays where it was — the arbiter's grant register and the CPU's
execute stage — so this is congestion and a second clock network on the die, not a new
path through the sink. `scripts/soc_timing_sweep.py --compare uart-final jtag-sink`.

**Both paths are verified on hardware, end to end, including the failure path (#114).**
JTAG: the CPU held in reset over ER1, a 2438-byte payload staged, the reset released, and
the bootloader answering `staged image: 2438 bytes, crc 8b0eb054` / `crc ok; starting
payload at 00008000`, followed by the payload's own output — repeated at 16 KiB and 32 KiB
with `overflow 0` and the word count matching. Console: `scripts/soc_payload.py` against
the shell's `load`, `staged 2476 bytes, crc a8cb0673; rebooting` → `payload running at
00000400`. `scripts/soc_jtag_stage.py --corrupt` damages one image word and leaves the
header alone, and the bootloader reports `staged image failed its CRC (2)` and falls back.

**Two source files cite 34 ms/word** — `ecp5-test/riscv/vexii_bootram.py` and
`firmware/cynthion-soc/src/main.rs`, from a harness that no longer exists. `--benchmark`
measures 28.0 ms through the same shape on r1.4; the difference is autodetection and
session overhead.

**Adjacent hard negative** — an FPGA-side *bitstream* loader is impossible on this part.
The ECP5 has no fabric path into its own configuration engine: the complete primitive list
is `JTAGG`, `OSCG`, `SEDGA`, `DTR`, `USRMCLK`, `GSR`, and none accepts configuration data.
The ~3 s of a configure is spent getting the image through the SAMD11, a **full-speed
12 Mbps** device, on control transfers costing 204 us each plus 2.53 us/byte. The 388 Mbps
that argument reasons from belongs to the FPGA's own PHY, not to the path the bitstream
takes. See `ecp5-test/loader/bitstream_sink.py` and issue #108, whose shipped result was
**713.9 → 322.2 ms, 2.22x**, from buffer sizing, double-buffered staging and DMA-clocked
JTAG.

### 16. Emulation and simulation

| | **QEMU `-M virt`** | Renode | Verilator | Amaranth sim |
|---|---|---|---|---|
| On the record here | yes, `scripts/soc_test.py` | **never considered** — no mention anywhere in the tree or in git history | **never considered** — no mention outside vendored `repos/` | yes, as targeted reproducers |
| Proves | firmware logic above the register map | — | — | gateware behaviour, cycle by cycle |
| Cannot prove | anything below the firmware; anything needing a reboot to survive | — | — | that the fault is fixed *on silicon* |

QEMU's value is subtractive: **it removes the peripheral from the question.** If the shell
misbehaves there the bug is in firmware logic; if it behaves there and not on the board,
the bug is below the firmware. That argument holds only because both builds are the same
source — `--features qemu` selects a different list of base addresses in `src/target.rs`, a
flash stand-in, and a RAM array in place of three HyperRAM MMIO primitives, and nothing
else.

Its limit is exactly the failure this SoC actually suffered: a read with a side effect
sharing a 32-bit word with the polled register — **in the console peripheral, not above
it**. A test against a re-implemented console driver could not have seen it, and did not.

Configuration notes: `-M virt -cpu rv32 -m 64M -bios none`. `-bios none` matters — the
default is OpenSBI, which loads at 0x80000000, exactly where `.text` goes. Addresses were
read out of `-machine dumpdtb` plus `dtc`, not assumed. Budgets: `BOOT_S = 5.0` (QEMU's own
startup measures well under 0.5 s), `REPLY_S = 3.0` (the shell replies in well under a
millisecond of wall time under TCG).

The Amaranth simulations are named reproducers rather than a framework:
`scripts/riscv_flash_crossbar_sim.py` (600-cycle starvation), `scripts/soc_serial_sim.py`
(measures the transmitter directly), `scripts/soc_plic_sim.py` (26 checks),
`scripts/soc_bus_sim.py` (9), `scripts/soc_board_sim.py` (31, measuring I2C intervals in
cycles against a model slave), `scripts/uart16550_sim.py`, `scripts/riscv_flash_jedec_sim.py`,
`scripts/soc_i2c_owner_sim.py`, `scripts/soc_irq_log_check.py`.

**"Fixed in simulation" is the claim this project has most often found wanting** — hence
the `grant` output on the SPI crossbar and the flash ILA.

### 17. Register access: transcription vs generated PAC

| | hand-transcribed constants | **generated PAC (addresses only)** |
|---|---|---|
| Source of truth | a human reading `vexii_hello_soc.py` | `HelloSoC.decoder.bus.memory_map` → `soc.svd` → svd2rust 0.37.1 → `base.rs` |
| Drift | silent | rename a peripheral and the firmware stops compiling |
| Enforcement | — | the `socmap` check; it found `hyperram.rs` carrying `BOOTRAM_BASE` by hand on its first run |
| Scale | three peripherals added by hand in one sitting | 12 peripherals, 55 registers, 96 fields |
| `.text` cost | — | unchanged at 16,430 bytes |

luna_soc's own SVD generator is excluded by policy and did not work anyway: it formats
`csr_base` with `{:08x}` and every window in this design reports `csr_base = None`.

**Why svd2rust's register accessors are deliberately unused — two independent reasons:**

1. **Portability.** A PAC generated from our map hardcodes our bases, so a driver written
   against `pac::console` could not run under QEMU, and the shared source that makes the
   test gate evidence about the board would be gone.
2. **Granularity-8 shadow registers.** Every CSR sits behind an `amaranth_soc` multiplexer
   with a granularity of 8 bits, where a multi-byte register is read by latching a shadow
   from its **lowest** byte and written by committing on its **highest**. svd2rust emits
   one natural-width volatile access per register — a `u16` read for a 16-bit register —
   which is **a different bus transaction** from the two ordered byte accesses the hardware
   specifies. Worked example: `Gpio::set_mode` in `firmware/cynthion-soc/src/gpio.rs`.
   Read the low byte first, or the high byte is whatever the shadow held.

What the memory map cannot carry: prose. `csr.Register` rewrites `__doc__` from a
template, so every register in the design reports "A CSR register."

### 18. Logging from an interrupt handler

| | format in the handler | **deferred ring** (`firmware/cynthion-soc/src/events.rs`) |
|---|---|---|
| Cost | `Uart::put` waits for `LSR.THRE` — on a level-sensitive shared source that is not a delay, **it is a hang** presenting as a dead CPU with a running clock | four stores: a code, a 64-bit payload in two halves, and the instant |
| `core::fmt` | a dispatch through `Arguments`, a conversion per value, a call per fragment | done in the main loop, on a console it owns |
| Under a storm | stalls the handler | drops the record, increments a counter, **reports the count** |

This project has mistaken that hang for dead gateware more than once.

Enforced in two layers, because either alone is insufficient:

  * **Ownership.** `main` owns the `Uart` values and passes them by `&mut`; a handler is a
    free function with nothing to be handed one from. No global `print!`, no logging
    singleton, no `static mut` in the crate. `irq.rs` takes `UartRx`, which has no transmit
    method and no `core::fmt::Write`, so `write!` there does not compile.
  * **Grep.** Rust's privacy is per-module-tree, so a private item in the crate root is
    nameable from every child module. `scripts/soc_irq_log_check.py` (the `irqlog` check)
    fails any module containing a handler that mentions `write!`, `writeln!`, `fmt::Write`
    or `Uart`.

A rule with no alternative gets worked around rather than followed, which is why a handler
*can* log — it just cannot be what formats and transmits.

Ring: 16 entries of 16 bytes, **256 bytes** of block RAM. `push` clears `mstatus.MIE` for
the copy — four stores, no loop, still
wait-free, and free in a handler since the hardware already cleared MIE on trap entry. The
alternative, a compare-exchange loop to reserve a slot, would be lock-free rather than
wait-free and would still need the payload published separately.

**Where the ring is the wrong shape: a UART overrun (#128).** The discovery is in the same
place — an LSR read inside the handler — but the report has to survive the conditions that
produce it, and a push is DROPPED when the ring fills, under exactly the load that causes
an overrun. So `src/uart.rs` ORs the LSR error bits into a per-console `AtomicU8` that the
main loop prints and clears. Bits cannot be lost, only coalesced; the count of reads that
saw one is a separate `AtomicU32` the `irq` command prints. The ring is for events with
arguments worth reading individually, which this is not.

**The record encoding (#124):** a 32-bit code with a typed 64-bit payload, tag in the
code's upper bits (0 none, 1 u8, 2 u16, 3 u32, 4 u64, 5 f32, 8 f64, 16 8×u8, 17 8 ASCII),
never a string. The entry is 16 bytes with no padding spent, so indexing is a shift. Float
caveat: the CPU is `rv32imac` with no F extension, so *formatting* floats pulls in
compiler-builtins float code — the tags are reserved, the formatters are not implemented.

### 19. RTIC, or preemption without it

**Open — #201, with #115 for the RTIC half.**

**The question.** One defect is measured: **a turn of the main loop is unbounded, and
every deferred thing waits for it** — a 50 ms poll with a **61 ms worst gap**, and under a
device-emulation workload **1,266 µs worst case with 700 of 2,000 deadlines missed**.
Nothing else on the list is a defect: the shell is 0.10–0.23% busy, the round-robin is
fair, nothing is dropped, and `RINGS` is correct. Only preemption fixes the turn, and
preemption is one of RTIC's four separable parts — so the question is whether RTIC's other
three are worth their difference over preemption alone.

Zephyr is not that question (#112): it needs the same peripherals plus a board port, a
devicetree and drivers, and moondancer already has the structure an RTOS supplies, minus
the scheduler an event-driven USB device does not need.

**What preemption alone is worth**, hand-written in `src/dispatch.rs` behind
`--features preempt`, on the identical arrival sequence
([`soc-workload-and-preemption.md`](soc-workload-and-preemption.md)):

| | superloop | preemption |
|---|---|---|
| worst arrival → handled | 1,266 µs | **271 µs** |
| past the 375 µs deadline | 700 / 2,000 | **0 / 2,000** |
| `.text` | — | **+440 bytes**, 70 instructions per dispatch (1.7% of the work) |

**What each runtime costs**, same skeleton, same `opt-level = "z"`, against the language
floor (`scripts/soc_model_probe.py`,
[`soc-concurrency-models.md`](soc-concurrency-models.md)):

| model | runtime `.text` | of the 4 KiB I-cache | RAM |
|---|---|---|---|
| cooperative, hand-written | 224 | 5% | — |
| hand-written **preemption** | 440 | 11% | — |
| Embassy 0.10 | 1,048 | 26% | grows per task |
| RTIC 2.3, `riscv-clint-backend` | 1,552 | 38% | **+1,332** in `.uninit` |

With moondancer's real control path in the tasks rather than a counter, RTIC is **+1,568,
38.3%** ([`rtic-usb-port.md`](rtic-usb-port.md)) — the real workload made it more
expensive, not less.

**The cache is the budget, and it does not fit either way.** The hot footprint under load
is **5,632 bytes against a 4 KiB direct-mapped cache**, before any runtime is added, and
`.text` understates footprint by **1.2x** (the dispatcher's 440 bytes occupy 512), which
projects RTIC at ~47%. That projection is modelled from QEMU traces, not measured on
silicon.

**Two structural findings, neither a matter of configuration.**

  * **No RTIC subset gives preemption alone.** Both generic RISC-V backends are
    `riscv-slic` backends and the SLIC *is* how RTIC preempts; the monotonic and the
    resource locking are the droppable parts. `riscv-slic`'s API is `critical_section::with`
    throughout, so taking the SLIC takes a global interrupt disable on every `pend`.
  * **RTIC cannot bind a hardware interrupt.** `binds =` names a SLIC source, so no task
    can consume the PLIC front end and `src/irq.rs` survives adoption with the SLIC in
    series behind it. The event queue — the piece carrying the longest hand-written
    correctness argument — is exactly what the ceiling analysis cannot reach. The trade is
    not "compile-time correctness for cache"; it is correctness for *some* shared state,
    for cache.

**What would settle it is board-only**, and #115 should not close before it exists: RTIC's
own I-cache footprint and latency under this workload, the cost of `critical_section::with`
per `pend` and `lock`, and real IPC and miss counts from the CPU's performance counters —
`./dev.py test`'s `ipc 1.000` is the host TSC under QEMU and has never measured anything.
The last question has no runtime number at all: whether checked resource access is worth
1,112 bytes over the dispatcher.

Hardware timers cut across it: three comparators against one `mtimecmp` is 1,188 bytes of
`.text` against 1,336 and 8 bytes of `.bss` against 40 — that 148 bytes *is* the software
timer queue, and the set-in-the-past race goes with it — but `rtic_time::Monotonic` and
`embassy-time` each want exactly one `set_compare`, so cheap comparators erode the case for
both frameworks.

### 20. Multi-transaction device protocols

**Settled (#123).** The PAC1954 has a state machine spanning transactions: REFRESH, then
roughly 1 ms in which reads are NACKed — the part acknowledges its address and then NACKs
the register pointer.

| | read on demand | wait-and-retry (`cbbafe4`) | **one owner, cached reads** (#123) |
|---|---|---|---|
| Behaviour | a hand-typed `power` lands inside the poller's REFRESH window about **2%** of the time and reports "no acknowledge (register pointer)" on a working bus | 2 ms wait, one retry | `power` prints the poller's cached sample and touches no bus |
| Verdict | the fault | symptom gone, structure that caused it retained | worst-case staleness 50 ms, imperceptible on a console |

Generalisation: **each device's multi-transaction protocol has exactly one owner; everyone
else reads a cached value.** Staleness is the kind of wrongness that looks right, so the
sample carries the instant of the REFRESH that latched it — not the read that fetched it —
and `power` prints its age. A poller that has stopped then reads as a number climbing past
50 ms instead of as four plausible voltages.

---

## Toolchain and dependencies

### 14. `amaranth-soc`: upstream vs vendored

| | luna_soc's vendored copy | **upstream `amaranth-soc`** |
|---|---|---|
| How it was reached | luna_soc appends its vendor dir to `sys.path` **only when `import amaranth_soc` fails** — and nothing declared the dependency, so it failed on every fresh environment | declared in `cynthion`'s `pyproject.toml`, from git |
| Version | reports `unknown`; four commits behind | `3e3d8b7` |
| Missing fixes | had a hand-backported py3.14 annotation fix; lacked `b4f8bb0`, `c9cd4cd`, `99d0837` | has all four |

The py3.14 bug: `if hasattr(self, "__annotations__")` in `csr/reg.py` — on 3.14+ **every**
class has `__annotations__`, so the guard stops distinguishing anything. Fixed upstream in
`d8b5892` (2026-01-28). Moving from luna-soc 0.2.0 to 0.3.2 does not fix it; the fix exists
only in `amaranth-soc`, which luna-soc has not re-synced from.

The PyPI `amaranth-soc` is a placeholder — version "0", no modules — so the dependency is
declared from git.

Verified by three scripts of increasing strength, because an import proves the names exist
and only elaboration proves the shapes still connect:
`scripts/amaranth_soc_devendor_verify.py` (import → luna_soc modules → real designs to
RTIL/Verilog), `scripts/amaranth_soc_dropin_test.py` (a throwaway venv under `tmp/`), and
`scripts/patch_amaranth_soc_annotations.py`, now **obsolete** and kept only as a record of
what the bug was.

**Still open:** `cynthion` pins `luna-soc` to `awtoau/awto-luna-soc`. The fork existed for
an Amaranth API change and 40+ patched CSR classes; upstream is now ahead, so the
amaranth-soc reason is gone. Whether anything else still needs it has not been checked.

---

### 21. 16550: written from the spec vs a vendored core

**The default is to take a proven implementation and change its back end.** The survey that
tests that default here was run for #128:

| | **ours** (`ecp5-test/riscv/uart16550.py`) | OpenCores `uart16550` | RoaLogic `apb4_uart16550` |
|---|---|---|---|
| Language | Amaranth | Verilog-2001 | SystemVerilog (packages, packed structs, enums) |
| Licence | BSD-3-Clause, as the rest of this tree | **LGPL 2.1**, in every file header | BSD-2-Clause |
| Size | ~130 lines of logic | 12 files, ~135 KB | 5 files, ~65 KB |
| Bus | `amaranth_soc` CSR, granularity 8 | Wishbone B3 | APB4 |
| Proven by | 34 assertions in `scripts/uart16550_sim.py`, plus QEMU parity through `soc_test.py` | two decades in OpenRISC/OpenCores SoCs | its own Verilator bench |
| Back end | `amaranth.lib.stream` ports | RS-232 bit engine, **instantiated inside `uart_regs.v`** | RS-232 bit engine, separate files |
| Has | DR, OE, FE, THRE/TEMT, IIR read-to-clear, 16-byte FIFOs | all of that plus character timeout, per-character error tags, break detection, RX trigger levels | same as OpenCores |

There is no Amaranth- or Migen-native 16550, so vendoring means a Verilog black box —
which is a road already taken here: `ecp5-test/riscv/vexii_cpu.py` instantiates
`VexiiRiscv.v` through `platform.add_file`.

**Why ours stays.**

  * **The back end is the surgery, and in the mature core it is not at the boundary.**
    OpenCores instantiates `uart_transmitter` and `uart_receiver` *inside* `uart_regs.v`
    (lines 379 and 399) and derives the register semantics from their internals: `lsr6`
    reads the transmitter FSM's `tstate`, `lsr5` its `tf_count`, and PE/FE/BI come out of
    the receive FIFO as tag bits stored beside each byte (`rf_data_out[0:2]`). Cutting the
    bit engine off means editing the one file that holds every register meaning — modifying
    the proven part, which forfeits the proof. What would be inherited is the register file,
    which is the half that is cheap to write and cheap to assert.
  * **Neither of this board's two ports wants a stock back end.** The console is a USB CDC
    byte pipe: no baud rate, no start or stop bits, so a stock core could only be left
    unmodified by feeding its serial pins through a serialiser and a matching deserialiser —
    a divisor and a shift register's latency invented so that a module could be told it was
    a UART. The Apollo port genuinely is a serial line, but on pins shared with JTAG, which
    needs an output enable held across the stop bit, an idle qualifier and a pad
    synchroniser. A stock 16550 has none of those; that is issue #113, and `serial_line.py`
    is the answer to it.
  * **Licence.** The most-proven candidate is LGPL 2.1 and this tree is BSD-3-Clause, as is
    everything it builds against. RoaLogic's BSD-2 would be fine, and its separation is
    cleaner, but it is APB4 SystemVerilog with a package — a bus adapter and a yosys
    SystemVerilog dependency on top of the same back-end surgery.
  * **The memory map would stop being generated.** `scripts/soc_generate_pac.py` reads
    `amaranth_soc` memory maps and emits the SVD the firmware's addresses come from. A black
    box has no memory map, so the peripheral's description would go back to being
    hand-written — which is exactly what decision 17 exists to prevent.

**What a vendored core would not have solved either way:** the granularity-8 CSR semantics
(a multi-byte register latches a shadow on its low byte and commits on its high byte), the
`sync`↔`usb` crossing, and the elastic buffering sized per transport. Those are ours
whatever sits in front of them.

**What was taken from the proven core instead: its behaviour, as the specification.**
`uart_regs.v` was read line by line against ours while #128 was implemented, and it caught
two divergences that assertions written from our own understanding would not have —
THRE meaning "FIFO empty" rather than "FIFO has room", and IIR's idle encoding. That is the
principle applied at the level where it pays here. The other half is already in place: the
driver is exercised against QEMU's `ns16550a`, a proven implementation, on every run of
`scripts/soc_test.py`.

**Revisit if** a third transport appears that genuinely is an RS-232 line with no shared
pins, or if character timeout and per-character error tagging turn out to be wanted. Both
argue for the bit engine we currently have no use for.

### 22. What is resident at 0x0

With the shell resident and the payload slot not, the half of block RAM that grows is the
half pinned at the reset vector. It reached **36,514 bytes against 32,768** and stopped
linking.

| | shell resident (was) | **bootloader resident** (#138) |
|---|---|---|
| at `0x0` | shell + every command, 36,514 B | `firmware/cynthion-boot`, **492 B** |
| above it | payload slot, 32 KiB, usually empty | the image: shell, commands, drivers, 63 KiB |
| grows | the resident half | the replaceable half |
| a new command costs | resident space | image space |

**Cutting the address space is free.** The decoder sees one 64 KiB window at `RAM_BASE`;
the boundary is a linker fiction, so no DP16KD granularity applies and it can sit at any
address. 1 KiB is sized from the measurement — 492 bytes of image and 80 of stack at the
deepest call — not chosen as a round number.

**Both images ship in one bitstream.** Block RAM init covers all 64 KiB, so
`vexii_hello_soc.py` packs the bootloader at `0x0` and a default image at `IMAGE_ORIGIN`.
Power-on behaviour is unchanged: nothing is staged, so the bootloader jumps to what the
bitstream placed.

**One failure path, and no policy.** No magic, a bad CRC, a rejected length, a silent
HyperRAM — all jump to the image region. The bootloader does not retry, does not branch
on the reason and does not clear the header; clearing is a policy and lives in
`scripts/soc_jtag_stage.py --clear`. It carries no UART and no `core::fmt`, and leaves
two breadcrumbs instead: a status word at `0x3fc` that `info` renders, and a byte on the
FPGA_ADV sideband that Apollo can read with no CPU running.

**The fallback is the region, not the bitstream.** Block RAM survives a CPU reset, so
once an image has been copied in it *is* the image region until the FPGA is configured
again. Something always answers at the image origin — that is what makes the single
failure path safe — but `--clear` alone does not restore the bitstream's image, and
reconfiguring (about a second) is what does. Measured, not assumed.

**Revisit if** the bootloader needs protection from a bad image writing over it. That
means a second decoder window rather than a linker boundary, and the decode path is what
`18c1fa5` had to register to recover Fmax — so it costs timing, and the linker's
`ASSERT`s plus a bounds check on the staged length are what stand in for it today.

## What is not decided

| | state |
|---|---|
| Concurrency model (decision 19) | open — #201, #115 |
| HyperRAM Wishbone adapter | `HyperRAMWishbone` wraps upstream's controller at `0x20000000`, 8 MiB, `main=1 exe=1` (#90). DQS path unfinished (#92). Upstream's `HyperRAMInterface`/`HyperRAMPHY` measure 220.2 MB/s, 92.8% of theoretical |
| `luna-soc` fork pin | see decision 14 |
| Board platform vendoring | in progress: `CynthionPlatformRev1D4` is 206 lines of pins plus a 134-line base, but reaching it inherits `LUNAApolloPlatform` → `LUNAPlatform` and pins the luna-soc fork. Target is a platform depending only on `amaranth`, `amaranth.build`, `amaranth_boards.resources` |

## Unverified

| claim | where | what is missing |
|---|---|---|
| Renode as an emulation alternative | — | never evaluated on the record |
| Verilator as a simulation alternative | — | never evaluated on the record |
| Type-C physical attach/detach | commit `bd7867b` | interrupt path verified; a real attach has not been exercised |
| The port request grants CONTROL | decision 25 | simulated frame-exact against Apollo's matcher; no bitstream built, and Apollo has never been put into `FPGA_ADV_MODE_UART` from the host |
| The bootloader's sideband byte | `firmware/cynthion-boot` | written on every boot and **never read back** — nothing host-side speaks the sideband protocol to Apollo. `scripts/sideband_decoder.py` decodes a reply; no tool fetches one |
