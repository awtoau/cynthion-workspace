# Alternatives weighed, and why

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
| 8 | [PLIC source granularity](#8-per-device-plic-sources-vs-or-ed) | OR-ed for the Type-C pair | settled |
| 9 | [I2C topology](#9-i2c-three-controllers-vs-one-plus-a-mux) | one controller plus a mux | forced |
| 10 | [I2C register map](#10-i2c-register-map) | OpenCores rev 0.9 | settled |
| 11 | [SPI flash crossbar](#11-spi-flash-crossbar) | ours | upstream defect |
| 12 | [SPI flash CS hold](#12-spi-flash-chip-select-hold) | ours | upstream defect |
| 13 | [UART pad output enable](#13-uart-pad-output-enable) | ours | upstream defect |
| 14 | [`amaranth-soc` source](#14-amaranth-soc-upstream-vs-vendored) | upstream package | settled |
| 15 | [Firmware loading](#15-firmware-loading) | USB bulk + HyperRAM | untested end to end (#114) |
| 16 | [Emulation](#16-emulation-and-simulation) | QEMU `-M virt` + targeted sims | settled |
| 17 | [Register access](#17-register-access-transcription-vs-generated-pac) | generated PAC, addresses only | settled |
| 18 | [Logging from handlers](#18-logging-from-an-interrupt-handler) | deferred ring | settled (#122/#124) |
| 19 | [RTOS](#19-rtos-bare-loop-vs-rtic-vs-zephyr) | bare loop | **open** (#112/#115) |
| 20 | [Device ownership](#20-multi-transaction-device-protocols) | one owner, cached reads | in progress (#123) |
| 21 | [16550: written vs vendored](#21-16550-written-from-the-spec-vs-a-vendored-core) | ours, spec-checked against OpenCores | settled (#128) |

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
(2026-07-29), core plus block RAM only:

| variant | LUT4 | FF | BRAM | Fmax | closes 60 MHz |
|---|---|---|---|---|---|
| VexRiscv `cynthion` | 4739 | 1683 | 5 | 64.9 MHz | yes |
| VexRiscv `cynthion+jtag` | 5410 | 1832 | 5 | 58.4 MHz | no |
| VexRiscv `imac+dcache` | 4476 | 1712 | 7 | 55.9 MHz | no |
| VexRiscv `imc` | 3934 | 1452 | 3 | 47.4 MHz | no |
| VexiiRiscv base | 3497 | 1548 | 0 | synth only | |
| VexiiRiscv +rva | 3490 | 1557 | 0 | synth only | |
| VexiiRiscv +caches | 3870 | 2171 | 6 | synth only | |
| VexiiRiscv moondancer-like | 4126 | 2256 | 6 | synth only | |

Caveats stated in the log itself: BRAM counts are low because a CPU with no firmware
never drives its bus and synthesis prunes the attached memory; CoreMark is not in this
matrix.

**The earlier report these figures replace** is
[`luna_ecp5_fpga/riscv32_equivalence_and_variation_report_2026-07-22.md`](luna_ecp5_fpga/riscv32_equivalence_and_variation_report_2026-07-22.md).
Its headline comparison — 12646 LUT4 for VexRiscv against 6876 — **is not a like-for-like
figure**: the VexRiscv number includes the whole USB fabric and the VexiiRiscv rows do
not, so it overstates VexRiscv by roughly the size of a USB stack. Its two configurations
also differed in three ways at once (caches, atomics, supervisor mode), which is why
`cpu_matrix.py` exists. Its benchmark rows, which remain usable as an
apples-to-apples pair between *its own* two configurations: CoreMark total ticks
6,133,969 vs 6,361,949 (+3.7%), DMIPS/MHz 0.74 vs 0.63.

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
— [`riscv-clock-ceiling.md`](riscv-clock-ceiling.md) §1.

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

**One deliberate deviation was made and then reversed: reading IIR used to clear
nothing.** The reasoning was that IIR at +2 shares a 32-bit word with RBR at +0, so a
state-changing read there could strobe RBR if the CPU widened a byte access — the same
shape as the bug this peripheral replaced. THRE was made level-derived instead.

It was hardening against the wrong thing, and #128 reversed it:

| | what was assumed | what was measured |
|---|---|---|
| Does this CPU widen a byte read? | it might | **no.** `LsuCachelessWishbonePlugin` drives a single-byte `sel` (`VexiiRiscv.v:7499-7515`), found during the PAC work |
| Does the bridge strobe the other lanes? | unknown | **no.** `amaranth_soc.csr.wishbone` asserts `r_stb` per lane, `sel_index & ~we` |
| Could a cache line fill reach it? | unknown | **no.** the console is in a `main=0` PMA region |
| What actually protects the poll loop? | the absence of side effects | **the layout.** LSR at +5 is a different 32-bit word from RBR at +0, which holds whatever IIR does |

The cost of the divergence was the property the standard map was chosen for. A driver that
sets ETBEI, takes the interrupt, reads IIR and returns — which is what the standard says to
do and what every 8250 driver does — got an interrupt storm. Read-to-clear status is a
common, well-understood idiom; fighting it means fighting every 16550 driver ever written
to buy a guarantee the layout already gives.

Two further divergences were found while restoring it, both of which would have broken a
stock driver and neither of which was deliberate:

  * **LSR.THRE reported "the transmit FIFO has room" rather than "the transmit FIFO is
    empty".** The standard's promise is the second one, and Linux's 8250 takes it up:
    `serial8250_tx_chars` writes `up->tx_loadsz` — 16 — bytes after seeing THRE once. Against
    the old bit that is fifteen bytes into a FIFO with room for one. OpenCores agrees:
    `assign lsr5 = (tf_count==5'b0 && thre_set_en)`.
  * **IIR read `0xc3` at rest**, holding the transmit id in bits 3:1 while bit 0 said "no
    interrupt pending". The standard's table has one entry for idle, `0b0001`.

LSR's error bits are now reported too — OE, FE and the bit 7 summary, cleared by the read
that reports them. They were previously wired to zero for the same withdrawn reason. The
peripheral cannot see an overrun itself: its `sink` backpressures, so a full FIFO there is
a stall and not a loss, and the transport reports a destroyed byte on a pulse. Only the
Apollo port can produce one — a line with no flow control — because the USB CDC endpoint
NAKs while its buffer is full and the host retries.

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
the same property that made the console a standard 16550. Second reason: RTIC's RISC-V
backend and every generic Rust PLIC driver (`riscv-peripheral`) expect this register map,
so a non-standard controller means writing an RTIC backend before RTIC can be used
(decision 19).

It also satisfies the register discipline without adjustment: the only side-effecting read
is the claim at 0x200004, alone in its 32-bit word, and `pending` — the register a poll
loop reads — is at 0x001000, two megabytes away.

**What it cost, and what got it back.** Adding the PLIC and the second UART
(commit `26ef424`) dropped Fmax on `sync` from 76.8 MHz to 60.8–67.5 MHz against a 60 MHz
constraint — as little as 1.3% margin. The diagnosis was corrected in `18c1fa5`: the
critical path was **16.45 ns of which 13.64 ns was wire**, and no logic optimisation
shortens a path that is 83% routing. A flip-flop in the Wishbone response path:

| | before | after |
|---|---|---|
| Fmax on `sync` (4 builds) | 62.4–71.7 MHz | 79.2–80.7 MHz |
| spread | 9.3 MHz | 1.4 MHz |
| single transfer | 1 cycle | 2 cycles |
| burst beat | 2 cycles | 3 cycles |
| cache line refill | — | 1.5x as long |
| utilisation | — | +420 LUT, +33 FF; block RAM unchanged at 42 of 56 |

Verified: 26 PLIC checks, 16 QEMU checks, 9 bus checks; 24 interrupts taken with 0 stalls.
`scripts/soc_plic_sim.py` caught its own first-draft bug — it never compared priorities, so
the last source examined always won and the priority registers were stored, read back and
ignored.

### 8. Per-device PLIC sources vs OR-ed

**OR-ed, for the Type-C pair only.** Both FUSB302B `int` lines go to one source (4).

| | per-device sources | **OR-ed** |
|---|---|---|
| What it would buy | knowing which device asserted without a read | — |
| Reality | with one muxed controller only one device is addressable at a time, and the handler must read the mux's `LINES` register to decide which to service either way | one claim/complete pair instead of two |

Two devices × (`int` + `fault`) would be six signals; they do not need six sources.

**The trap that comes with a shared level, and how it is handled.** Clearing the condition
means reading three read-to-clear registers over I2C — about a millisecond at 80 kHz — on
the single controller the power monitor's poll is also using: a long spin in interrupt
context *and* a second master on a peripheral with no lock. So the handler does **not**
clear it. It masks the source and records the event through the ring (decision 18); normal
context clears every asserting device and re-enables. **The storm cannot happen, because
the source is off for the whole window in which the line is still high**; the re-fire
becomes one more deferred pass with the CPU making progress in between.

`fault` is deliberately kept out of the OR and polled: it means something different from
`int` and is worth telling apart without a register read.

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

| | `ecpbram` placeholder | JTAG staging | **USB bulk + HyperRAM** |
|---|---|---|---|
| Time per change | ~1 s | **34 ms per 16-bit word**, USB round-trip bound → ~9 min for 32 KiB | a few seconds of serial transfer |
| Image ceiling | block RAM | HyperRAM (8 MiB) | HyperRAM (8 MiB) |
| Coupling | every rebuild needs a matching magic placeholder file; a stale `.config` silently yields the wrong firmware | needs an Apollo debug session | none — the CPU writes HyperRAM itself |
| Arbiter | — | JTAG and CPU both reach HyperRAM | none: same master in two phases |
| Verdict | works, rejected as a coupling | **slower than the 60 s rebuild it existed to replace** | chosen |

`ecpbram` locates the old contents **by value**, and a real firmware image is ~87% zeroes,
which also fill every unused BRAM tile on the die — so it refuses with "Conflicting from
pattern" unless the design was synthesised against a known *random* placeholder. The
observed failure is in `tmp/logs/soc_swap_firmware.log`:

    firmware: 8268 bytes into 16384 words (65536 bytes of block RAM)
    ecpbram failed:
    Conflicting from pattern for bit slice from_hexfile[3071:2816][0]!

HyperRAM rather than straight into the target slot because the next step is a reboot, and
a reboot is what block RAM does not survive intact — the shell doing the receiving is
executing from it. The bootloader's CPU-side read port moves one 16-bit word at a time
with no FIFO and no side-effecting read: roughly **8 ms for a 32 KiB image at 60 MHz**,
against the ~60 s it replaces. Bursting is available later.

*Unverified:* the 34 ms/word JTAG figure appears in `ecp5-test/riscv/vexii_bootram.py` and
`firmware/cynthion-soc/src/main.rs`, both introduced in `b27f196`, both saying "measured"
with no harness or log cited. No underlying artefact survives in the tree.

*Also missing:* `scripts/soc_swap_firmware.py` is referenced by `vexii_hello_soc.py`
(three times) and by `vexii_bootram.py`, but the file is not in the tree — only its log.

**Status: untested end to end (#114).** `load` → reboot → `try_boot` has never been run on
hardware; each half is exercised separately. The QEMU gate cannot cover it, because the
emulated staging buffer lives in `.bss` and does not survive `j _start`.

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
| Cost | `Uart::put` waits for `LSR.THRE` — on a level-sensitive shared source that is not a delay, **it is a hang** presenting as a dead CPU with a running clock | three stores: a code and two `u32`s |
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

Ring: 16 entries, **192 bytes** of the 32 KiB the shell half of block RAM gives us.
`push` clears `mstatus.MIE` for the copy — three or four instructions, no loop, still
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

**Open (#124): the record encoding.** A 32-bit code with a typed 64-bit payload, tag in the
code's upper bits (0 none, 1 u8, 2 u16, 3 u32, 4 u64, 5 f32, 8 f64, 16 8×u8, 17 8 ASCII).
Entry size `u32` + `u64` is 12 bytes; padding to 16 makes indexing a shift, and is worth
taking. Float caveat: the CPU is `rv32imac` with no F extension, so *formatting* floats
pulls in compiler-builtins float code — reserve the tags, do not implement the formatters.

### 19. RTOS: bare loop vs RTIC vs Zephyr

**Open.** Both #112 and #115 are open and no decision has been made.

| | **bare loop today** | RTIC (#115) | Zephyr (#112) |
|---|---|---|---|
| Needs | — | a PLIC — **now present** | the same peripherals, plus a board port, a devicetree and drivers |
| Blocked by | — | nothing known | moondancer's linker script puts `.text`/`.rodata` in SPI flash (#93) |
| ISA | `riscv32imac` | matches | matches |
| Prior art | — | `riscv-peripheral` expects our PLIC map | VexiiRiscv is a supported Zephyr target; Zephyr's `memc_mcux_flexspi_w956a8mbya.c` is where this board's HyperBus command encoding was confirmed |
| Argument against | — | — | **it does not reduce the work, it adds to it**; moondancer is ~1,400 lines of Rust on `riscv-rt` with a generated PAC, a HAL, real handlers and `critical-section` — the structure an RTOS provides, minus a scheduler it does not need |

The current firmware is built to be RTIC-ready rather than committed to it: the standard
PLIC (decision 7), `Plic::set_threshold` for critical sections, and a monotonic time source
in `src/clock.rs`. The reason the console was moved off polling at all is that **RTIC
cannot be layered on a polled main loop.**

### 20. Multi-transaction device protocols

**In progress (#123).** The PAC1954 has a state machine spanning transactions: REFRESH,
then roughly 1 ms in which reads are NACKed — the part acknowledges its address and then
NACKs the register pointer.

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

**The default is to take a proven implementation and change its back end. That default was
not applied when this peripheral was written, and no candidate was looked at.** Recorded
here because the omission is the interesting part: the decision that follows survives the
review, but it was not made on the evidence at the time.

The survey, run for #128:

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

## What is not decided

| | state |
|---|---|
| RTOS (decision 19) | open — #112, #115 |
| HyperRAM Wishbone adapter | unavoidable; whether it wraps upstream's controller or replaces it is open (#90). DQS path unfinished (#92). Upstream's `HyperRAMInterface`/`HyperRAMPHY` measure 220.2 MB/s, 92.8% of theoretical |
| `luna-soc` fork pin | see decision 14 |
| Board platform vendoring | in progress: `CynthionPlatformRev1D4` is 206 lines of pins plus a 134-line base, but reaching it inherits `LUNAApolloPlatform` → `LUNAPlatform` and pins the luna-soc fork. Target is a platform depending only on `amaranth`, `amaranth.build`, `amaranth_boards.resources` |

## Unverified

| claim | where | what is missing |
|---|---|---|
| JTAG staging at 34 ms per 16-bit word | `ecp5-test/riscv/vexii_bootram.py`, `firmware/cynthion-soc/src/main.rs` | no harness or log; both sites introduced in `b27f196` |
| `scripts/soc_swap_firmware.py` | referenced four times | the file is not in the tree |
| Renode as an emulation alternative | — | never evaluated on the record |
| Verilator as a simulation alternative | — | never evaluated on the record |
| Type-C physical attach/detach | commit `bd7867b` | interrupt path verified; a real attach has not been exercised |
| Firmware staging end to end | #114 | each half exercised, the round trip is not |
