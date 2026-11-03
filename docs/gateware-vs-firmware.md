# Gateware or firmware: every decision the SoC bakes at elaboration

One row per **decision**, not per file. What each peripheral fixes when it
elaborates, whether that is the CPU's to make, and — when it stays — the
mechanical reason.

**Index:** [`README.md`](README.md) · siblings [`instruments.md`](instruments.md)
(why a firmware read-back beats a gateware compare), [`soc-interrupts.md`](soc-interrupts.md)
(capture and edge detection, the clearest case for staying).

Subsumes [#323](https://github.com/awtoau/cynthion-workspace/issues/323) (what the SoC bakes),
[#315](https://github.com/awtoau/cynthion-workspace/issues/315) (`<peripheral>_init()`),
[#311](https://github.com/awtoau/cynthion-workspace/issues/311) and
[#327](https://github.com/awtoau/cynthion-workspace/issues/327) (pins). Row-to-issue
map at the end.

Scope, as built: **23 peripheral modules** (24 files, one is `__init__.py`),
**32 `elaborate` methods**, **63 module-level constants in
[`top.py`](../gateware/soc/top.py)**, plus
[`clocks.py`](../gateware/soc/clocks.py) and [`cpu/`](../gateware/soc/cpu/).

## Seven reasons to stay in gateware

[#531](https://github.com/awtoau/cynthion-workspace/issues/531) names four, and
all four hold. Three more turned up that none of the four covers; each is named
below with why it is not one of the existing ones.

| # | reason | the mechanical form it takes |
|---|---|---|
| **R1** | inside a transaction the CPU cannot meet the timing of | a signal asserted unconditionally with no ready/valid to hold it |
| **R2** | must run before firmware exists | a `reset_less` domain, or a counter that gates the CPU's own reset |
| **R3** | capture, latching, edge detection | the event is narrower than the CPU's sampling interval |
| **R4** | a rate the bus cannot sustain | the CPU delivers a 32-bit beat every 3 `sync` cycles |
| **R5** | **the CPU is the thing being stalled** | the logic drives `bus.ack` for a cycle the CPU is waiting inside — a CPU-side decision deadlocks by construction, at any clock |
| **R6** | **it is not a decision** | metastability resolution and reset-domain crossing carry no policy; there is nothing for firmware to own |
| **R7** | **the CPU's own clock is what is being measured** | firmware's time base is the quantity under test, so its answer is degenerate |

R5 is not R1 with a smaller number. R1 is a race the CPU loses; R5 is a
circular dependency it cannot win at any speed — the instruction that would
make the decision cannot retire until the decision is made.
[`flash.py:255`](../gateware/soc/peripherals/flash.py) and
[`block_ram.py:125`](../gateware/soc/peripherals/block_ram.py) are the two, and
the flash one also services I-cache refills for `.text`, so there is not even an
instruction stream to run the decision from.

R6 covers every `FFSynchronizer`, `AsyncFIFOBuffered` and `AsyncFFSynchronizer`
in the tree. Naming it matters because otherwise each one gets re-argued as a
timing case.

R7 has exactly one row — [`clock_monitor.py`](../gateware/soc/peripherals/clock_monitor.py) —
and is listed because none of the other six covers it. A `sync` at half the
intended rate yields the same count from a CPU-run counter and merely takes
twice the wall-clock time to produce it; the measurement needs a second time
base that cannot itself be wrong, which is the discrete A8 oscillator.

## What it costs today, ranked

Rebuild cost: **~90-142 s synthesis per bitstream** (not measured on this tree's current netlist).

| # | decision | what it costs today | evidence |
|---|---|---|---|
| **1** | The Apollo console's baud is **elaboration-frozen through a port built to be a register** | `SerialLine.divisor` is an `In` port with `init=divisor`; `top.py` drives only the streams | [`serial_line.py:171-177`](../gateware/soc/peripherals/serial_line.py) vs [`top.py:1632-1634`](../gateware/soc/top.py) |
| **2** | The 16550's DLL/DLM are **"written, read back, and connected to nothing"** — its own words | the register that would carry item 1 already exists, in the peripheral the console already talks to | [`uart16550.py:315-320`](../gateware/soc/peripherals/uart16550.py), writes `:358-366`, reads `:355`/`:363` |
| **3** | **A benchmark cannot name the bitstream that produced it** — and the escape hatch does not close it either | `FLASH_MODE`, `FLASH_DIVISOR`, CK and the cache geometry reach **no** CSR and **no** PAC constant. USERCODE is `git rev-parse --short=7 HEAD` masked to 31 bits ([`build_helpers.py:69-85`](../gateware/build_helpers.py)) — **a function of the commit alone**, so two variants from one clean commit are stamped identically, and [`usercode_map.py:167`](../gateware/usercode_map.py) keys its index on that number, so the second build silently overwrites the first's row | [`top.py:582`](../gateware/soc/top.py), `:609`, `:735`, `:748`, `:824-825`; absent from [`soc_generate_pac.py`](../scripts/soc/soc_generate_pac.py) |
| **5** | Nine signals commented **"Exposed for instrumentation"** are read by nothing | the ILA that consumed them was deleted in `620901b`; three of the nine never had a matching port on it even then | [`flash.py:402-414`](../gateware/soc/peripherals/flash.py), driven `:536-549` |

Item 5 is the [`instruments.md`](instruments.md) shape: a number that cannot
move, presented as a measurement.

## The table

`CPU's?` = should the RISC-V own this decision. `stays` rows name the reason;
`moves` and `param` rows name what it needs.

### Flash

| peripheral | decided at elaboration | CPU's? | reason / what it needs |
|---|---|---|---|
| [`flash.py`](../gateware/soc/peripherals/flash.py) mmap FSM `:147-257` | Wishbone slave sequence | no | **R5** — `:255` drives `bus.ack` for a cycle the CPU is stalled in, and this window is also the I-cache refill path for `.text` |
| …`READ_MODES` `:85-97` | opcode, lane widths, dummy bits | **param** | `:199-202` and `:212` are **Python** `if`s on a Python `int`: in `"single"` the `DUMMY` and `DUMMY-RET` states **do not exist**. The states must be built unconditionally before the mode can be a register |
| …`divisor` `:396` | SCK rate | **param, but not free** | three netlist effects: the clock generator's counter width, `:526`'s guard folding to constant 1, and `:528` `sr_in_shift.eq(divisor == 0)` folding to a constant. **Divisor 0 and divisor 1 are different circuits**, not one circuit with a different count |
| …`MMAP_DEFAULT_TIMEOUT = 256` `:144` | 4.27 µs chip-select hold | **param** | a bet on CPU locality, with no readback |
| …`cs_delay = 0` `:433` | tCSS setup | **param** | a hardcoded local, so the `WaitTimer` branch at `:435-441` is dead in every build |
| …`byteorder` `:247-248`, `hasattr(pads.cs, "oe")` `:458-459` | two more Python conditionals | note only | the `hasattr` is never taken — `PinSignature`'s `cs` has no `oe` — and would change the design silently if the resource grew one |
| …crossbar `locked` `:314` | — | **yes, read-only** | genuinely unobservable. `grant` **does** reach a CSR (`top.py:1243`), into the dead readout at rank 4 |
| …`HoldableSPIController` CS hold | already a CSR at `:614` | — | **R4 for the residue** — the CPU issues CSR writes far slower than the PHY drains the TX FIFO, so software cannot keep `tx_fifo.r_rdy` asserted; only the `held | inner.cs` OR is gateware |
| …the inner controller's `cs` CSR at `0x04` | — | delete | written by nothing. [`flash.rs:17`](../firmware/cynthion-soc/src/flash.rs) says so: "a write PULSE; cannot hold chip select. Unused here" |
| [`block_ram.py`](../gateware/soc/peripherals/block_ram.py) | `size` → EBR count | no | **R5** — `:125` acks a cycle the CPU is stalled in, for the memory holding its own stack. `init` is the one input that needs no resynthesis ([`bram_patch.py`](../scripts/bram_patch.py)) |
| …`inside` `:102-103` | bounds mask | note | constant 1 at `RAM_SIZE = 32 KiB`. The module exists to allow a non-power-of-two size and the shipping size is a power of two; `top.py:150-151` still states the retired constraint |

### Serial, I2C, and the board

| peripheral | decided at elaboration | CPU's? | reason / what it needs |
|---|---|---|---|
| [`i2c_master.py`](../gateware/soc/peripherals/i2c_master.py) bit engine | 200 ns slots, 5 per bit | no | **R1/R4** — a CSR peripheral answers in 5 cycles *before* the LSU and uncached `iobus` round trip ([`bus/fault.py:88`](../gateware/soc/bus/fault.py)), two pins need two stores, and an SDA edge while SCL is high is a START to every device on the segment |
| …**PRER** | nothing — a plain RW register | — | **the model.** `prescale_for()` is exported from the gateware (`:192-200`), called by [`soc_generate_pac.py:649-661`](../scripts/soc/soc_generate_pac.py) with the *solved* clock, and re-derived at run time from `clock::measured()` ([`bus.rs:186-190`](../firmware/cynthion-soc/src/bus.rs)). [`i2c_rate_sweep.py`](../scripts/soc/i2c_rate_sweep.py) walks the whole ladder from **one bitstream** |
| …`SLOTS_BIT`, `SLOTS_COND` | t_LOW:t_HIGH split | **param** | `:178-181`. A driver can compute f_SCL and cannot learn the split |
| [`i2c_mux.py`](../gateware/soc/peripherals/i2c_mux.py) select hold | move only at `idle` | no | **R1** — the window is opened by a CSR write firmware has *already issued*: the write completes in ~5 cycles, the transfer it must not disturb is 540 |
| …`applied` `:205-208` | — | **yes, read-only** | driven and read, but `_select` is `csr.action.RW`, so firmware reads its own request back, never the pins. Both FUSB302Bs answer 0x22 with the same identity byte, which is exactly where a wrong-bus read looks plausible |
| …`BUS_RESOURCES` `:75-79` | — | delete or use | in `__all__`, imported by nothing; `top.py:2017-2019` and `:2041-2043` restate the same mapping twice more |
| [`uart16550.py`](../gateware/soc/peripherals/uart16550.py) register map | 16550A layout | no | not a timing case at all — it exists so Linux's `autoconfig` passes (`:531-538`) |
| …`thre_int` `:487-497` | edge-latched transmit-empty | no | **R3** — derived from level `LSR.THRE`, the standard handler sequence is an interrupt storm caused by firmware behaving correctly |
| …`FIFO_DEPTH = 16` `:162` | both FIFOs | **param** | IIR[7:6] only says "a 16550A"; the depth is inferred from a part number |
| …DLL/DLM `:315-320` | — | **wire it** | rank 2 |
| …FCR bits 7:6 | receive trigger level | note | silently discarded; with IIR id `0b110` never raised, a Linux 8250 gets neither a trigger level nor a character timeout |
| [`serial_line.py`](../gateware/soc/peripherals/serial_line.py) bit engine | 8.68 µs per bit | no | **R1** — `tx_oe` is *counted*, not inferred: at divisor 8 `phy.tx.rdy` rises at cycle 80 while the stop bit occupies 80..87, so the line is undriven for the whole stop bit (`:56-62`) |
| …idle qualifier `:212-228` | 12 bit periods | no | **R1** — it gates `phy.rx.i` every `sync` cycle so the PHY's FSM cannot leave IDLE. Filtering *bytes* instead leaves the receiver resynchronising its bit phase to noise |
| …`divisor` port | reset value only | **yes — already a port** | rank 1 |
| …`armed`, `frame_errors` `:183-184` | — | **yes, read-only** | both documented "Diagnostic:", both driven, and the only reader in the repository is [`soc_serial_sim.py`](../scripts/soc/soc_serial_sim.py). So the failure the module documents as its known trade — the receiver disarms and a back-to-back sender never gives it 12 idle bits — is not observable by any means the product has |
| [`stream_buffer.py`](../gateware/soc/peripherals/stream_buffer.py) flush `:148-158` | — | no | **R2** — the host types during PLL lock and the flush must fire while `sync` is still in reset. Note `ResetSignal(..., allow_reset_less=True)` at `:150` returns `Const(0)` if the write domain is ever declared `reset_less`, which deletes the fix with no error and no test |
| …`depth` 16/16/64/16 | LUT RAM vs BRAM | **param** | `top.py:322-336`. Zero CSRs: no occupancy, no high-water mark, no evidence of a drop — on the one path where loss is possible, because [`ns16550a-console-uart.md:198`](chips/ns16550a-console-uart.md) records that `SerialLine` does not honour `ready` |
| [`clock_monitor.py`](../gateware/soc/peripherals/clock_monitor.py) | `WINDOW_CYCLES = 60_000` = 1 ms | no | **R7.** The gate is correctly baked: the latched count *is* kHz only because the window is exactly 1 ms, so a register here would redefine what the other register means. Three limits below |
| [`i2c_master.py`](../gateware/soc/peripherals/i2c_master.py) `sda_o` | constant 0 | no | correct for open-drain: the pad is driven low or released, never high |

`clock_monitor`'s three limits, none of which change the verdict: its reference
window is `m.d.usb`, so **no window completes for the first 1.2 ms** while the
ULPI POR holds `usb` in reset — a clock monitor reporting "no measurement yet"
because a *USB PHY* is preparing; a **stopped reference oscillator freezes
`measured` with the valid bit still set**, which the file's own "what it cannot
tell you" section does not cover; and `OSCILLATOR_KHZ = 60_000` (`:68`) is a
fourth copy of `clocks.USB_PHY_MHZ` rather than an import, against the rule
[`clocks.py:128-130`](../gateware/soc/clocks.py) states.

### Reset, POR and the board

| peripheral | decided at elaboration | CPU's? | reason / what it needs |
|---|---|---|---|
| [`clocks.py`](../gateware/soc/clocks.py) ULPI POR `:401-428` | 128 + 72,000 `usb` cycles | no | **R2**, three ways over. It is `reset_less` (`:332`) because `usb`'s reset *is* `~phy_ready` (`:439`) — a counter in `usb` would be held at 0 for ever. It does not wait for the PLL, so it starts at configuration. And warm reconfiguration does not power-cycle the USB3343, so its internal POR does not re-run; before [#241](https://github.com/awtoau/cynthion-workspace/issues/241) the only recovery from a glitched PHY was to reconfigure the FPGA, which the firmware on that FPGA cannot do |
| …`PHY_PAD_RESET_CYCLES = 128` `:120`, `PHY_PREP_CYCLES = 72_000` `:131` | 2.133 µs (2.13× the 1 µs minimum), 1.20000 ms (TPREP's **maximum**) | **param** | already constructor parameters at [`ulpi_window.py:151-152`](../gateware/soc/peripherals/ulpi_window.py) — and used as such **only by simulation**, never by the hardware path |
| …`SYNC_CEILING_MHZ` `:112` | nothing | — | pure refusal gate, deliberately outside `VARIANT_ENV` because they change no netlist. The right shape: fail in 0 s rather than 200 s into nextpnr |
| [`ulpi_window.py`](../gateware/soc/peripherals/ulpi_window.py) CSR reset sequencer `:391-409` | the same two counts | **not a duplicate** | its FSM is `domain="usb"`, and `usb` is in reset until `phy_ready` — so it **physically cannot start before the POR finishes**. No ordering to get wrong. It is also narrower on purpose: TARGET's pad only (AUX carries the console), the `ULPIRegisterWindow` submodule only, and it is *reportable* through `STATUS.resetting`. The narrowing is what firmware would have to re-implement, not the 1.2 ms wait |
| …`TIMEOUT_CYCLES = 4096` `:125` | 68.27 µs | **param** | ~400× headroom over a register read's "under ten cycles". Transcribed by hand into [`ulpi.rs:66,78`](../firmware/cynthion-soc/src/ulpi.rs) as `SETTLE_US = 86` and `RESET_US = 1_503`; the sim checks the gateware constants against the datasheet and nothing checks the firmware bounds |
| [`vbus_csr.py`](../gateware/soc/peripherals/vbus_csr.py) reset state | all four switches open | no | **R2**, and it is the cleanest instance. `enable` is `csr.action.RW` with `init=0`; the gate is **combinational** (`:128-133`), not a latched copy; the pads are active-high `Pins`, so 0 is open with no inversion to get wrong. The state exists between configuration and the first store instruction — there is no instruction stream in that interval |
| [`sideband_csr.py`](../gateware/soc/peripherals/sideband_csr.py) | bit positions only | — | the model for the rest: no baked timing at all |
| [`fabric_status.py`](../gateware/soc/peripherals/fabric_status.py) | `DTR_PERIOD_BITS = 19` `:110` | **moves** | a free-running 19-bit counter, **8.738 ms** at 60 MHz, against a conversion that takes 8 cycles. A write-to-start bit plus the `valid` readback that already exists at bit 7 replaces it. The cycle count is baked and its wall-clock meaning floats with `SYNC_MHZ`: 10.49 ms at 50 |
| …the build's own configuration | **deliberately absent** | correct, and incomplete | `:16-30` refuses to put constants in fabric — *"folded logic that moves with its value"* — and routes identity to USERCODE, clocks and cache to the build record. Correct in principle; see rank 3 for where the routing does not arrive |

### CPU, clocks and the build itself

| peripheral | decided at elaboration | CPU's? | reason / what it needs |
|---|---|---|---|
| [`cpu/intc.py`](../gateware/soc/cpu/intc.py) trigger per source | level vs edge | no | **R3** — `trigger=` is a constructor argument to `amaranth_soc.event.Source`; it decides which flops exist. There is no mux and no register. The table and its per-device justification: [`soc-interrupts.md`](soc-interrupts.md) |
| …8 of 18 lines | tied low | by design | source number = bit position, so wiring one up renumbers nothing. Each still costs a pending/enable pair |
| [`cpu/cpu.py`](../gateware/soc/cpu/cpu.py) cache geometry | `CACHE_SETS`, `CACHE_WAYS` | no | genuinely per-bitstream — SpinalHDL generation, then synthesis. [`soc_cache_sweep.py`](../scripts/soc/soc_cache_sweep.py) is the right shape for it |
| …performance counters | count only | note | the plugin arrives with `--with-rdtime` regardless, so dropping the flag generates a **byte-identical** core; the count is the only free variable |
| …two unconnected core outputs | `ndmreset`, `debug_stoptime` | **bug** | an openocd `ndmreset` reaches nothing, and `mtime` keeps advancing while the hart is halted, so `mtimecmp` deadlines pile up across a breakpoint and the tick storms on resume |
| [`cpu/clint.py`](../gateware/soc/cpu/clint.py) | `mtime` compare | no | RISC-V standard; nothing dead |
| [`variant.py`](../gateware/soc/variant.py) | 2 env axes | — | `CYNTHION_HYPERRAM_CK_MHZ`, `CYNTHION_SYNC_MHZ`, each hashed into the slug |
| …`VEXII_ROOT` | the core's source checkout | **bug** | **not hashed.** [`build_helpers.py:41-66`](../gateware/build_helpers.py) digests `gateware/**/*.py` plus `variant.settings()`, so a submodule bump produces a different core with an identical slug — the "served the previous bitstream as freshly built" failure of [#294](https://github.com/awtoau/cynthion-workspace/issues/294) |

## Ports that exist and reach nothing

[`scripts/soc_dangling_ports.py`](../scripts/soc_dangling_ports.py) finds these
by name across `gateware/soc/**`, with probe and simulation use reported
*beside* the verdict rather than folded into it — a lever only a JTAG probe
drives is still baked as far as firmware is concerned. `--check` asserts six
known-dangling ports are found with the right verdict and five known-wired ones
are not, so a pass carries information.

61 candidates of 200 port names; the confirmed ones, by class:

**Tied to a literal** — the [#327](https://github.com/awtoau/cynthion-workspace/issues/327)
shape at the module boundary:

* `fabric_reconfigured` ← `top.py:1809` — deliberate, and `top.py:1804-1805`
  says why: nothing in this design has ever had anything to report

**Driven, read by nothing in the SoC** — a diagnostic that cannot report:

`armed`, `frame_errors` (`serial_line.py`) · `phy_ready` (`clocks.py`) ·
`own` (`sideband_csr.py`) · the nine `o_*` in `flash.py`

`phy_ready` is the one with a named consequence. `sync` is released when the PLL
locks — tens of µs — while `usb` stays in reset for 1.20213 ms. A ULPI access in
that window leaves `busy` set, [`ulpi.rs:66`](../firmware/cynthion-soc/src/ulpi.rs)
bounds the wait at 86 µs, and returns `Error::NoPeripheral`, whose documented
meaning is *"a bitstream/firmware mismatch"*. So "the PHY is still preparing" is
reported as "the wrong bitstream", and the one bit that distinguishes them is
invisible. `usb3343_init` is on that path.

`own` also shows why the checker that was supposed to catch this class does not:
[`check_ports_connected.py:63-75`](../scripts/tools/check_ports_connected.py) searches
a haystack that includes the declaring file, so `self.own.eq(own)` inside the
peripheral's own `elaborate` satisfies it. **Any output driven inside its own
module passes.** The two checkers disagree and the weaker one is the one on the
gate.

**Nothing drives it, anywhere** — an input at its `init` for the life of the
bitstream:

`grant` (`flash.py`)

## Switched off, with a reason

Not the same as the list below. Each of these is built code that a recorded
decision keeps out of the netlist, which is an elaboration decision like any
other row above.

| module | why | where the reason is |
|---|---|---|

## Merged, in no layer yet

A different question from the table, and not answerable by moving anything: this
is code with no caller. Listed so it is not mistaken for a misplaced decision.

* **`BUS_RESOURCES`** ([`i2c_mux.py:75-79`](../gateware/soc/peripherals/i2c_mux.py)) —
  in `__all__`, imported by nothing; `top.py:2017-2019` and `:2041-2043` restate
  the same mapping twice more
* **`StreamBuffer.depth`**, **`BlockRAM.size`** and
  its `init` setter — properties with no reader. [`soc_stream_buffer_sim.py:77`](../scripts/soc/soc_stream_buffer_sim.py)
  re-declares its own `DEPTH` rather than reading the one that exists

SBU is the largest instance of this and is **out of scope here** (parked, #591).

Two dangling references found on the way:
`scripts/riscv_flash_crossbar_sim.py` and `scripts/riscv_flash_ila_decode.py`
are cited five times — `flash.py:283`, `flash.py:560`, `top.py:1172`,
[`board/core.py:17`](../gateware/board/core.py) and
[`upstream-boundary.md`](upstream-boundary.md) — and **neither file exists**.

## Corrections

Where the framing this audit was given turned out to be wrong.

| claim | correction |
|---|---|
| [#531](https://github.com/awtoau/cynthion-workspace/issues/531): `FLASH_DIVISOR` is the type case | it is the weakest of the ranked items, on its own evidence. `top.py:599-603` records the sweep as **already done and all-pass** — 60 points, five modes × four divisors × three sync rates, up to 144 MHz SCK, nothing failed. The binding limit is that SCK derives from `sync`, not the divisor. And the change is not free: divisor 0 and divisor >0 are **different netlists** (`flash.py:526`, `:528`), so a CSR must build the divisor>0 circuit and give up divisor 0's extra shift beat |
| [#531](https://github.com/awtoau/cynthion-workspace/issues/531): "24 modules" | 24 files, one of which is `__init__.py`. Two more are switched off by a recorded decision, and two are merged-but-unwired and out of scope, so the table covers 19 |
| [#327](https://github.com/awtoau/cynthion-workspace/issues/327) / [#305](https://github.com/awtoau/cynthion-workspace/issues/305): `control_vbus_in_en`/`aux_vbus_in_en` are `Out()` ports reaching no pad | **fixed.** `VbusControl` has exactly four outputs (`vbus_csr.py:111-114`) and all four reach pads (`top.py:1889-1896`); the post-mortem is at `vbus_csr.py:15-22` and the matching firmware note at [`vbus.rs:129-131`](../firmware/cynthion-soc/src/vbus.rs). The stale artefact is a doc: [`chips/ecp5/pin-usage.md:141-142,186-190,455`](chips/ecp5/pin-usage.md) still calls it "the worst finding here" |
| [#323](https://github.com/awtoau/cynthion-workspace/issues/323): `gateware_id.py:243-249` | **the file does not exist** — replaced by `fabric_status.py` in `8a915b3`. The DTR finding survives verbatim at `fabric_status.py:110,175-191`; the "`GatewareId` reports `sync_hz`, `usb_hz` and the cache geometry" claim is now understated, because **no** fabric register reports any of them |
| [#531](https://github.com/awtoau/cynthion-workspace/issues/531): four reasons | R5 and R6 above are neither of the four, and between them account for most of the `stays` rows |
| [#323](https://github.com/awtoau/cynthion-workspace/issues/323): "`grant` and `locked` have no readback" | half stale — `grant` has one, into a CSR no firmware reads; `locked` has none |

Line numbers in [#323](https://github.com/awtoau/cynthion-workspace/issues/323)
have drifted throughout, and one file it cites is gone.

Three defects found while checking the above, none of them a
gateware-versus-firmware question, all filed separately:

* [`ulpi_window.py:399-406`](../gateware/soc/peripherals/ulpi_window.py) —
  `window_reset` resets the `window` submodule only; the outer transaction FSM
  (`:322-354`) is not under the `ResetInserter`, so it stays in `RUN`,
  `window.done` cannot assert, and `elapsed` reaches 4096. The misreport the
  comment disclaims is exactly what still happens, and the start gate at `:242`
  does not exclude `resetting_sync`
* the POR is ready after **72,129** cycles (`clocks.py:427`) and the CSR
  sequencer acks after **72,128** (`ulpi_window.py:407`) — same two constants,
  one cycle apart
* [`cpu/cpu.py:465-525`](../gateware/soc/cpu/cpu.py) leaves two core outputs
  unconnected: an openocd `ndmreset` reaches nothing, and `mtime` advances while
  the hart is halted, so `mtimecmp` deadlines pile up across a breakpoint and
  the 1 ms tick storms on resume

And one comment that disagrees with its own value: `top.py:640-654` argues at
length for `SYNC_MHZ = 50` — *"50, and it is measured … 4 of 4 seeds"* — while
the shipping default is **60** (`variant.py:66`).

## Filed from this audit

| issue | rows |
|---|---|
| [#533](https://github.com/awtoau/cynthion-workspace/issues/533) | ranks 4, 5 — instrumentation whose reader was deleted |
| [#535](https://github.com/awtoau/cynthion-workspace/issues/535) | rank 3 — provenance, and `VEXII_ROOT` not hashed |
| [#536](https://github.com/awtoau/cynthion-workspace/issues/536) | ranks 1-2 — the console baud, and `armed`/`frame_errors` |
| [#537](https://github.com/awtoau/cynthion-workspace/issues/537) | the port checker that cannot fail, and the dangling-port inventory |
| [#538](https://github.com/awtoau/cynthion-workspace/issues/538) | four defects found on the way, none of them layering questions |
| [#539](https://github.com/awtoau/cynthion-workspace/issues/539) | the stale references |

## Row to issue

| issue | rows it corresponds to |
|---|---|
| [#323](https://github.com/awtoau/cynthion-workspace/issues/323) | the whole table — this is its sweep, re-derived rather than copied |
| [#315](https://github.com/awtoau/cynthion-workspace/issues/315) | the `stays`/**R2** rows are its boundary: `stream_buffer` flush, the DDRDLL settle, the ULPI POR |
| [#311](https://github.com/awtoau/cynthion-workspace/issues/311) | pin drive and slew — outside this table, because they are bitstream *configuration bits*, patchable without resynthesis, not fabric registers |
| [#327](https://github.com/awtoau/cynthion-workspace/issues/327) | the "tied to a literal" class, one level up: [#327](https://github.com/awtoau/cynthion-workspace/issues/327) is about pads, this is the same shape at a module boundary |
