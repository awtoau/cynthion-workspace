# Where the SoC's size actually is

**A measured account of what the RISC-V SoC costs — LUTs and flip-flops in the
fabric, bytes of `.text` and `.rodata` in the firmware — and of the measurement
hazards that make several of the obvious ways to ask the question give wrong
answers.**

This is a reference, not a plan. What follows are numbers with the conditions
they were taken under; what to *do* about any of them is an issue. See
[`README.md`](README.md) for that rule.

**Reproduce with**, none of which touch the board:

| script | answers |
|---|---|
| [`soc_peripheral_area.py`](../scripts/soc_peripheral_area.py) | what each peripheral costs synthesised alone — an upper bound |
| [`soc_instrumentation_cost.py`](../scripts/soc_instrumentation_cost.py) | what the three instrumentation peripherals cost **in context** — a delta |
| [`soc_upstream_copy_check.py`](../scripts/soc_upstream_copy_check.py) | whether our verbatim copy of luna_soc's SPI PHY is still verbatim |
| [`pnr_noise.py`](../scripts/pnr_noise.py) | how much of an Fmax difference is placement luck |
| [`soc_icache_model.py`](../scripts/soc_icache_model.py) | what a `.text` layout costs in I-cache misses |
| [`soc_review.py`](../scripts/soc_review.py) | what moved since the recorded baseline, and what got duplicated — run it after every change |
| [`soc_map_audit.py`](../scripts/soc_map_audit.py) | how much of the decoded address space is mapped, and what an unmapped read does |
| [`soc_decoder_cost.py`](../scripts/soc_decoder_cost.py) | which knob drives the Wishbone decoder's cell count |

---

## 0. The two budgets are not the same budget

**Fabric size is LUTs and flip-flops.** The design has room: 58% of the LUT4 and
30% of the DFF on a die that is a 25F wearing a 12F's marking.

**Firmware size is SPEED.** The I-cache is 8 KiB, 64 sets x 2 ways.
`firmware/cynthion-soc/Cargo.toml` records `opt-level` `z` → `3` growing `.text`
79% and costing **5.4x the IPC**, and
[`rtic.md`](rtic.md) measures the
hot footprint at 5,632 bytes — larger than the cache before any runtime is added.
So in the firmware a library that is cleaner but bigger is a loss, and every
change needs a `.text` delta beside it.

## 1. Baseline

Measured at `b403978`, before the changes in §5.

| | |
|---|---|
| `.text` | 41,400 B |
| `.rodata` | 17,056 B |
| flash image | 58,480 B |
| bootloader | 512 B (`.start` 12 + `.text` 500) |
| TRELLIS_COMB | 14,178 / 24,288 (58%) |
| TRELLIS_FF | 7,479 / 24,288 (30%) |
| DP16KD | 44 / 56 (78%) |
| `$glbnet$clk` Fmax | 73.69 MHz against a 60 MHz constraint |
| `./dev.py test` | 97 checks |
| `./scripts/soc_sims.py` | 531 checks across 15 simulations |

## 2. Three ways to measure fabric area, and what each is good for

This matters more than any single number below, because two of the three are
routinely quoted as if they were the third.

**Out of context** — synthesise one module alone. Fast, works for a module the
top no longer instantiates, and is an **upper bound**: alone, a peripheral keeps
logic that would be folded away among its neighbours. `soc_peripheral_area.py`.

**In context** — build the whole SoC twice and diff the utilisation. This is the
delta, and it is the only kind of number worth acting on.
`soc_instrumentation_cost.py` does it without editing any tracked file, by
subclassing the modules under test with an empty `elaborate`.

**DFF is exact in both.** A register is a register wherever it is placed. LUT4
is mapping, and mapping depends on the neighbours; read the DFF column first.

### The hazard: `gateware_id`'s constants fold, so an A/B must pin them

`GatewareId` bakes its identity in as 32-bit constants and synthesis folds them,
so two different words land on different LUT counts. `soc_instrumentation_cost.py`
run without pinning them reported the instrumentation at **714** TRELLIS_COMB;
pinning `built` and `git` gives **1,074**. Same code, same question, 360 cells
apart, and the smaller number is the wrong one. DFF was 456 both times — which is
what "DFF is exact" means in practice.

**Measured**, 2026-08-12, shipping SoC `bist0-ck160-dqs1-merge0-sync60-mirror0-mirrordiv4`,
`--no-parallel-refine`, one firmware image, 3 builds an arm
([`soc_repro_arms.py`](../scripts/soc_repro_arms.py)):

| arm | distinct netlists | COMB spread | FF spread | `clk` spread |
|---|---|---|---|---|
| `built` varies, naming pinned | 3 | **276** | 0 | **7.95 MHz** |
| naming varies, `built` pinned | 3 | 0 | 0 | 0.00 MHz |
| both fixed (shipped) | **1** | 0 | 0 | 0.00 MHz |

- The 32-bit constant is the whole of the area and timing cost. The
  `id()`-derived module name changed the netlist BYTES and mapped, packed and
  placed identically — it broke reproducibility without costing a cell.
- Both still had to be fixed. Either one alone leaves three distinct netlists,
  and a netlist that is not byte-identical cannot be a control for anything.
- FF is flat in every arm. Only the MAPPING moved, never the function.
- Fixed in [#441](https://github.com/awtoau/cynthion-workspace/issues/441):
  `built` was `datetime.now()`, so a build a minute later was a different netlist.
  It is now the gateware source digest.
- Still pin them for an A/B. Both arms of an A/B edit the tree, so the digest
  moves with the edit — the constant is stable across TIME, not across SOURCES.
- [`soc_repro_check.py`](../scripts/soc_repro_check.py) is the gate: two builds of
  one tree, byte-compared, and `check.py`'s `repro` runs it.

### The floor: a COMB delta under ~200 is the mapper

- Null control, 2026-08-12, `432d29b`, `bist0-ck160-dqs1-merge0-sync60`,
  `--no-parallel-refine`: one 32-bit identity constant given a different value,
  no logic touched → **TRELLIS_COMB +194, TRELLIS_FF +1**
  ([`soc_trim_delta.py --trim null-constant`](../scripts/soc_trim_delta.py)).
- Global, not local folding: the whole `gateware_id` block is 153 COMB, and the
  move is 194.
- So a COMB delta is a result only when it is well clear of ±194 **and** an FF,
  BRAM or LUTRAM delta of the same sign supports it. Numbers this unsettles, and
  the trims that survive it:
  [#454](https://github.com/awtoau/cynthion-workspace/issues/454).
- [`soc_review.py`](../scripts/soc_review.py) enforces it: a smaller move is
  printed and explicitly not called a regression.

### The other hazard: Fmax is placement luck

Already known (71.45–76.99 MHz across identical builds) and confirmed again
here: 84.88 MHz and 73.69 MHz from two builds differing by 22 cells. No Fmax
figure in this document is offered as evidence of anything;
[`pnr_noise.py`](../scripts/pnr_noise.py) is what settles a timing question.

## 3. What each peripheral costs

Out of context, so upper bounds. `./scripts/soc_peripheral_area.py`.

| module | LUT4 eq | DFF | DP16KD | |
|---|---:|---:|---:|---|
| `hyperram_probe` | 750 | 289 | — | instrumentation |
| `clint` | 400 | 137 | — | |
| `plic` (5 sources) | 286 | 52 | — | |
| `uart16550` | 231 | 118 | — | **×2 in the SoC** |
| `i2c_master` | 200 | 109 | — | |
| `flash_probe` | 183 | 72 | — | instrumentation |
| `serial_line` | 162 | 99 | — | |
| `vexii_irq` | 149 | 101 | — | **in no SoC** |
| `ulpi_window` | 98 | 96 | — | |
| `stream_buffer` (64, sync) | 90 | 27 | — | |
| `flash_ila` | 89 | 108 | 1 | instrumentation |
| `sideband_csr` | 85 | 50 | — | |
| `gateware_id` | 80 | 26 | — | no DTR without a platform |
| `stream_buffer` (16, CDC) | 46 | 57 | — | |
| `vbus_csr` | 23 | 24 | — | |
| `i2c_mux` | 9 | 20 | — | |
| `wishbone_pipe` | 9 | 34 | — | |

Two things stand out. `hyperram_probe` is the **largest peripheral in the SoC**
— larger than the CLINT, larger than the PLIC, larger than either console — and
it is instrumentation. And `wishbone_pipe`, which recovered the design's timing
margin, costs nine LUT4 and 34 flops.

## 4. What the instrumentation costs, in context

`FlashILA`, `FlashPinProbe` and `HyperRAMProbe`, built into the real SoC and then
built again with their logic removed and their CSR bridges kept:

| cell | with | without | delta | share of the design |
|---|---:|---:|---:|---:|
| TRELLIS_COMB | 14,476 | 13,402 | **1,074** | 7.4% |
| TRELLIS_FF | 7,472 | 7,016 | **456** | 6.1% |
| DP16KD | 44 | 43 | **1** | |

Both builds pinned `GatewareId`'s timestamp and git hash; see the hazard in §2
for what the same measurement reports without that.

**This understates a deletion.** Each stub keeps the CSR bridge that answers at
its address, so a real removal recovers this plus three bridges and three
decoder windows.

The measurement that made the three worth counting together: they exist to
answer questions, and two of those questions are answered.
`peripherals/flash.py`'s own docstring says the ILA "was built to find the JEDEC read
fault, which is fixed", and `peripherals/flash_cdc.py` records that once the PHY moves to
its own domain the ILA's captures cross a clock boundary unsynchronised and are
"evidence about gross behaviour only".

### `ObservablePHY` is a 130-line verbatim copy, and it is still verbatim

`gateware/soc/peripherals/flash.py`'s `ObservablePHY` re-implements
`luna_soc`'s `SPIPHYController.elaborate` statement for statement, adding only
assignments that expose internals to the ILA. Its docstring states the
obligation — "If upstream changes, this must be re-synced or it is measuring a
different circuit than the one that ships" — and nothing checked it.

[`soc_upstream_copy_check.py`](../scripts/soc_upstream_copy_check.py) now does.
**As of the currently installed `luna_soc`: in sync.** 83 upstream statements,
85 in the copy, the two extra being the instrumentation assignments and the `as
fsm:` handle they need. The check was validated against known-bad mutations in
both directions before its passing result was believed.

## 5. Where the firmware's bytes are

By module, attributing every `.text` symbol (`llvm-nm --print-size`):

| | bytes | share |
|---|---:|---:|
| `cynthion_soc::run` — **one function** | 17,044 | 41.2% |
| unattributed / small symbols | 5,034 | 12.2% |
| `bench` (13 symbols) | 5,004 | 12.2% |
| `core::fmt`, `core::str`, `core::num` | 4,638 | 11.3% |
| `fusb302` | 1,746 | 4.2% |
| `board` | 1,002 | 2.4% |
| everything else | ~6,900 | 16.7% |

**`run` is 41% of `.text` in a single symbol.** It is a 25-arm `match` on the
command word, and `opt-level = "z"` with `lto = true` still inlines a function
called from exactly one place — so `map_command`, `board_led`, `board_i2c`,
`board_power`, `board_sideband`, `vbus_command`, `hyperram_command`, `help`,
`load` and the rest are all absorbed into it. 17,044 bytes is 266 cache lines
against 64 sets.

**Splitting it costs 668 bytes.** Measured: `#[inline(never)]` on ten command
handlers moves 7,000 bytes out of `run` (17,044 → 10,044) and grows `.text` from
41,400 to **42,068**, because the call sequences and the lost cross-inlining are
not free. Whether the locality is worth 668 bytes is **not measured** — the
addresses a command touches are the same either way, and only a linker that then
orders by hotness would change what the cache sees.
[`soc_icache_model.py`](../scripts/soc_icache_model.py) is what would settle it.

### `core::str` cost 880 bytes to compare four ASCII words

`vbus_command` was the only place in the firmware that touched `core::str`:

```rust
let argument = core::str::from_utf8(rest).unwrap_or("").trim();
```

`<str>::trim` consults `core::unicode::unicode_data::white_space::WHITESPACE_MAP`.
The console line arrives as `&[u8]`, `crate::trim` is 88 bytes and does the byte
equivalent, and every other parser in the firmware — `memory::Region::parse`,
`gpio::led_by_name`, `fusb302` — already takes bytes.

Measured, changing that one line and `vbus::Source::parse`'s signature:

| | before | after | delta |
|---|---:|---:|---:|
| `.text` | 41,400 | 41,016 | **−384** |
| `.rodata` | 17,056 | 16,560 | **−496** |
| flash image | 58,480 | 57,600 | **−880** |

`<str>::trim`, `str::validations` and `WHITESPACE_MAP` are absent from the image
afterwards. **An estimate from reading the code put this at 1,868 bytes.** The
measurement is 880. Both numbers are in this document because the gap between
them is the point.

## 6. What upstream actually supplies, and what it does not

Checked against the installed libraries by running them, not by reading them.

| we hand-roll | upstream has | verdict |
|---|---|---|
| `StreamBuffer`'s FIFO↔stream wiring | `amaranth.lib.fifo`'s `w_stream` / `r_stream` | **replaced** — byte-identical Verilog, §7 |
| `Plic` | nothing | `amaranth_soc.event.Monitor` **raises `DriverConflict` and does not elaborate**, as `upstream-boundary.md` says. Re-verified here; `csr.event.EventMonitor` fails the same way |
| `blockram` (from luna_soc) | `amaranth_soc.wishbone.sram.WishboneSRAM` | **not a swap.** Its signature carries no `cti`/`bte`/`err`, so cache-line refills lose registered-feedback bursting. Same 2 cycles a beat, fewer features |
| `flash_cdc.ClockCrossedPHY` | `luna_soc`'s own `SPIControlPortCDC` | ours uses `AsyncFIFOBuffered` where upstream uses `AsyncFIFO`, and `peripherals/flash_cdc.py` records that the unbuffered form put the FIFO output mux and the PHY's carry chain on one path. Ours is the fixed one |
| `SplitRW` (read and write are different registers) | nothing in `amaranth_soc.csr.action` | correct as written, and already shared by four peripherals |
| `SerialLine` | `amaranth_stdio.serial.AsyncSerial` | already a wrapper; the four things it adds are all things `AsyncSerial` deliberately leaves out |

### The luna_soc surface is three times what the doc says

[`toolchain-simplification.md`](toolchain-simplification.md) §2 states "the
genuine surface is exactly two things" — `core.blockram` (127 lines) and the
VexRiscv CPU (194). It predates the flash work. The shipping SoC also imports
the whole of `luna_soc.gateware.core.spiflash`:

    mmap.py 207   phy.py 246   port.py 172   controller.py 164
    utils.py  93   __init__.py 116                  = 998 lines

from `peripherals/flash.py` (`SPIFlashMemoryMap`, `SPIPHYController`,
`SPIClockGenerator`, `SPIController`, `SPIControlPort`, `StreamCore2PHY`,
`StreamPHY2Core`, `WaitTimer`, `PinSignature`), `top.py`
(`ECP5ConfigurationFlashInterface`) and `peripherals/flash_cdc.py` (`SPIControlPort`).

**1,125 lines, not 127.** The "321 lines against 4,078" trade in that document is
arithmetic on a surface that has since tripled.

## 7. Changes made, and what they measured

| change | `.text` | `.rodata` | fabric |
|---|---:|---:|---|
| `peripherals/stream_buffer.py` uses `fifo.w_stream` / `r_stream` | — | — | **byte-identical Verilog** |
| `vbus_command` and `vbus::Source::parse` take bytes | −384 | −496 | — |
| `bench.rs`: duplicate `#[inline(always)]` removed | 0 | 0 | — |
| `cpu/clint.py`: docstring pointed at a file that does not exist | — | — | — |

After: `.text` 41,016 · `.rodata` 16,560 · flash image **57,600** (−880) ·
TRELLIS_COMB 14,025 · TRELLIS_FF 7,481 · DP16KD 44 · `$glbnet$clk` 73.05 MHz ·
`./dev.py test` 97 checks · `./scripts/soc_sims.py` 531 checks in 15
simulations · `./dev.py lint` 8 passed.

Both check counts are **tree-state dependent** and that is worth knowing before
anyone reads a difference into one: the suite has a paired check on whether the
working tree is clean, so an uncommitted tree reports 96 and 530 and a committed
one 97 and 531. The numbers above are from the committed tree, as are the
baselines in §1.

The `stream_buffer` change was verified by converting both versions to Verilog
and diffing: the only differences are `src` attributes and the order of two
`assign` statements. `w_stream` and `r_stream` alias the FIFO's existing signals
rather than adding any, and `wiring.connect` checks the two signatures agree
where a hand assignment of `payload` to a differently sized `w_data` would
silently truncate.

## 8. Things that are wrong rather than merely large

**`peripherals/vbus_csr.py`'s `input` register drives nothing.** `VbusControl` exposes
`control_vbus_in_en` and `aux_vbus_in_en`, and `top.py` states that
the two pads are "deliberately NOT requested". So the register is writable, reads
back what was written, and reaches no pin — while `peripherals/vbus_csr.py`'s own docstring
says "`vbus input both` restores the permissive state at runtime" and
`vbus::inputs()` reports its value to the operator as though it were the state of
the board. A shell that says the AUX input is open when nothing has opened it is
worse than a shell with no such command.

**`gateware/soc/cpu/irq.py` is in no design.** 119 lines, 149 LUT4-equivalent
if it were ever instantiated. `upstream-boundary.md` keeps it so luna_soc's SVD
generator "still finds the map" — but `repos/cynthion`'s facedancer top imports
`InterruptController` from `luna_soc.gateware.cpu`, not from here, so nothing in
either tree reaches it.

**`hello_soc.py` claimed users it did not have** — its docstring said it "stays
as the smaller reference point that several scripts still build against", and
nothing built it. **Deleted**, with `cpu_area.py`: both existed to compare
VexRiscv against VexiiRiscv, a settled choice, and they were the only importers
of `luna_soc.gateware.cpu.VexRiscv` in the tree.

**Three doc references in `gateware/` point at files that do not exist** —
`docs/apollo_samd11_mcu/fpga-adv-sideband.md` (`sideband/sideband_gateware.py:5`),
`docs/luna_ecp5_fpga/hyperram-speed.md` (`hyperram/hyperram_identify.py:11,80`,
`hyperram/hyperram_fifo.py:109`) and
`docs/luna_ecp5_fpga/fast-bitstream-loading.md` (`loader/bitstream_sink.py:25`).
`./dev.py docs` checks links in Markdown and nothing checks them in source
comments, which is why these survived. A fourth, in `cpu/clint.py`, is fixed
in §7.

**`vbus::discharge()` has no caller** (`firmware/cynthion-soc/src/vbus.rs:305`).
Two other functions reported as dead in the same survey — `hyperram::seek_word`
and `hyperram::write_word_pub` — are called eleven times between them from
`bench.rs`, so this list is only the ones checked against the whole tree.

## 9. Two claims in the tree that measurement did not support

Recorded because both read plausibly and both are wrong in the same direction —
each makes a library look better than it is.

**`amaranth_soc`'s event monitors do elaborate.** They do not. Both
`event.Monitor` and `csr.event.EventMonitor` raise
`amaranth.hdl._ir.DriverConflict` on `pending` and `bus__r_data` respectively.
Reading their source suggests otherwise; running them does not.

**`WishboneSRAM` is a drop-in for luna_soc's `blockram`.** It is not. It
constructs its Wishbone signature with no `features`, so it has no `cti`, no
`bte` and no `err` — and the CPU's cache-line refills are registered-feedback
bursts. Its acknowledge also takes the same two cycles a beat that
`bus/wishbone_pipe.py` already documents for the block RAM, so there is no latency
argument either.
