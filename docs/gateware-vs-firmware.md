# Gateware or firmware: every decision the SoC bakes at elaboration

One row per **decision**, not per file. What each peripheral fixes when it
elaborates, whether that is the CPU's to make, and — when it stays — the
mechanical reason.

**Index:** [`README.md`](README.md) · siblings [`instruments.md`](instruments.md)
(why a firmware read-back beats a gateware compare), [`soc-interrupts.md`](soc-interrupts.md)
(capture and edge detection, the clearest case for staying).

Subsumes [#323](https://github.com/awtoau/cynthion-workspace/issues/323) (what the SoC bakes),
[#341](https://github.com/awtoau/cynthion-workspace/issues/341) (timing as levers),
[#315](https://github.com/awtoau/cynthion-workspace/issues/315) (`<peripheral>_init()`),
[#319](https://github.com/awtoau/cynthion-workspace/issues/319) (CR0/CR1),
[#311](https://github.com/awtoau/cynthion-workspace/issues/311) and
[#327](https://github.com/awtoau/cynthion-workspace/issues/327) (pins). Row-to-issue
map at the end.

Scope, as built: **23 peripheral modules** (24 files, one is `__init__.py`),
**32 `elaborate` methods**, **63 module-level constants in
[`top.py`](../gateware/soc/top.py)**, plus [`bootram.py`](../gateware/soc/bootram.py),
[`hyperram_share.py`](../gateware/soc/hyperram_share.py),
[`clocks.py`](../gateware/soc/clocks.py) and [`cpu/`](../gateware/soc/cpu/).
Default variant `ck80-dqs0-sync60`.

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

Rebuild cost: **~90 s synthesis per bitstream**
([`chips/hyperram/bist-plan.md:17`](chips/hyperram/bist-plan.md); the brief says
142 s — same order, and neither is measured on this tree's current netlist).

| # | decision | what it costs today | evidence |
|---|---|---|---|
| **1** | The HyperRAM controller's seven timing levers exist and **nothing in any bitstream drives them** | every tCSHI, watchdog and latency sweep is still one bitstream per point, with the ports already built | [`hyperram_controller.py:338-381`](../gateware/soc/peripherals/hyperram_controller.py); `recovery_cycles`, `burst_cycles`, `burst_beats`, `min_latency_clocks` are **not in `HyperRAMPort.CONTROL`** ([`hyperram_share.py:64-66`](../gateware/soc/hyperram_share.py)), so no master can reach them at all |
| **2** | `recovery_cycles` is **1 bit wide** at CK 80 | the lever cannot be moved even once wired: `_max_recovery_cycles = max(1, ceil(7.5 × 80 / 1000)) = 1`, so the CPU could write 0 or 1 | [`hyperram_controller.py:276`](../gateware/soc/peripherals/hyperram_controller.py), width at `:365`. Widening it is a rebuild — which is the axis [#341](https://github.com/awtoau/cynthion-workspace/issues/341) exists to remove |
| **3** | DQS `latency_clocks` is **3 bits** | any sweep past L=7 truncates silently; `hyperram_share.py:118-120` passes no `max_latency_clocks` | [`hyperram_dqs_controller.py:214-215`](../gateware/soc/peripherals/hyperram_dqs_controller.py) |
| **4** | `hr sweep` prints an `rd-stall` column that **varies nothing** | `sel[5:4]` reaches `BootRAM.read_stall_cycles`, which nothing drives; the sweep runs 64 points to move 16 | [`shell/hr.rs:183-198`](../firmware/cynthion-soc/src/shell/hr.rs), [`bootram.py:673`](../gateware/soc/bootram.py) |
| **5** | `hr cross` prints `ck-stalled {} cycles` where the number **can only be 0** | `clock_stop=False` → `want_stall` is `C(0)` → `probe_stall` is 0 → the `stalls` counter cannot increment | [`bootram.py:773-775`](../gateware/soc/bootram.py), [`:962`](../gateware/soc/bootram.py), [`hyperram_probe.py:130`](../gateware/soc/peripherals/hyperram_probe.py), [`bench.rs:499`](../firmware/cynthion-soc/src/bench.rs) |
| **6** | The Apollo console's baud is **elaboration-frozen through a port built to be a register** | `SerialLine.divisor` is an `In` port with `init=divisor`; `top.py` drives only the streams | [`serial_line.py:171-177`](../gateware/soc/peripherals/serial_line.py) vs [`top.py:1632-1634`](../gateware/soc/top.py) |
| **7** | The 16550's DLL/DLM are **"written, read back, and connected to nothing"** — its own words | the register that would carry item 6 already exists, in the peripheral the console already talks to | [`uart16550.py:315-320`](../gateware/soc/peripherals/uart16550.py), writes `:358-366`, reads `:355`/`:363` |
| **8** | **A benchmark cannot name the bitstream that produced it** — and the escape hatch does not close it either | `FLASH_MODE`, `FLASH_DIVISOR`, `HYPERRAM_LATENCY_CLOCKS`, `HYPERRAM_DQS`, CK and the cache geometry reach **no** CSR and **no** PAC constant. USERCODE is `git rev-parse --short=7 HEAD` masked to 31 bits ([`build_helpers.py:69-85`](../gateware/build_helpers.py)) — **a function of the commit alone**, so `ck80-dqs0-sync60` and `ck160-dqs1-sync50` from one clean commit are stamped identically, and [`usercode_map.py:167`](../gateware/usercode_map.py) keys its index on that number, so the second build silently overwrites the first's row | [`top.py:582`](../gateware/soc/top.py), `:609`, `:735`, `:748`, `:824-825`; absent from [`soc_generate_pac.py`](../scripts/soc_generate_pac.py) |
| **9** | `FlashPinProbe`'s five counters are **live gateware feeding a readout nothing reads** | `cs_fell`, `sck_edges`, `dq_driven`, `grants`, `oe_edges` reach `FLASH_PROBE = 0xf0000200`; no file under `firmware/cynthion-soc/src/` names it | [`flash.py:735-758`](../gateware/soc/peripherals/flash.py); [`soc_dead_peripherals.py:11`](../scripts/soc_dead_peripherals.py) already records it |
| **10** | Nine signals commented **"Exposed for instrumentation"** are read by nothing | the ILA that consumed them was deleted in `620901b`; three of the nine never had a matching port on it even then | [`flash.py:402-414`](../gateware/soc/peripherals/flash.py), driven `:536-549` |

Items 1-3 are one afternoon of wiring and remove the largest bitstream axis
still standing. Items 4, 5 and 10 are the [`instruments.md`](instruments.md)
shape: a number that cannot move, presented as a measurement.

## The table

`CPU's?` = should the RISC-V own this decision. `stays` rows name the reason;
`moves` and `param` rows name what it needs.

### HyperRAM

| peripheral | decided at elaboration | CPU's? | reason / what it needs |
|---|---|---|---|
| [`hyperram_controller.py`](../gateware/soc/peripherals/hyperram_controller.py) CA, latency and data phases | state sequence | no | **R1** — `write_ready` is a per-cycle comb default of 0 (`:498`) set unconditionally to 1 inside `WRITE_DATA` (`:716`), with no caller-side term. A stalled master re-writes the previous word into the next device location. Read side mirrors it: `read_ready` follows the device's `rwds.i == 0b10` (`:679-704`) with no acknowledge |
| …command phase `:590-612` | one state per cycle | no | **R1** — the CA is six bytes the device latches on consecutive CK edges; a bubble is a different command, not a delayed one |
| …RWDS extra-latency sample `:466-480` | sample at cycle 6 | no | **R3** — a 12.5 ns window six cycles into a transaction the CPU did not start |
| …`latency_clocks`, `low_latency_clocks`, `fixed_latency` | reset value only | **yes — already ports** | in `HyperRAMPort.CONTROL`, muxed at `hyperram_share.py:191-193`, and **neither face drives them**. Only the ceiling probe does ([`hyperram_ceiling_top.py:664-702`](../gateware/probes/hyperram/hyperram_ceiling_top.py)) |
| …`recovery_cycles`, `burst_cycles`, `burst_beats`, `min_latency_clocks` | reset value only | **yes — already ports** | absent from `CONTROL`; undriven in every build. Rank 1 above |
| …every signal **width** | `_max_latency_clocks`, `_max_recovery_cycles`, `_burst_cycles` | no | sized from constructor arithmetic. A width is a rebuild whatever the value does — ranks 2 and 3 |
| …`FITTED_GRADE`, `T_CSM_NS`, `T_CSM_MARGIN`, `PHY_ROUND_TRIP_CYCLES` | datasheet AC figures | **param** | `:103-139`. `PHY_ROUND_TRIP_CYCLES = 1` on the DQS twin is documented as the *simulation's* number, not the hardware's ([`hyperram_dqs_controller.py:77-80`](../gateware/soc/peripherals/hyperram_dqs_controller.py)) |
| …`state`, `timed_out`, `extra_latency`, `latency_below_trwr`, `register_active` | — | **yes, read-only** | driven, and `HyperRAMPort.STATUS` is only `(idle, read_ready, write_ready, read_data)` (`hyperram_share.py:70`), so a watchdog-terminated transaction is invisible to firmware |
| …`register_space` | tied to 0 on the staging path | **yes** | [`bootram.py:930`](../gateware/soc/bootram.py). **Not "driven by nothing"** — the BIST engine drives it live (`hyperram_ceiling_top.py:820`), so register access exists in BIST mode and not from the CPU's memory window |
| …`single_page` | tied to 0 by **every** synthesizable caller | no, but say so | `bootram.py:922`, `hyperram_share.py:384`, and four probes. CA[45] folds to a constant; only [`soc_hyperram_sim.py:786`](../scripts/soc_hyperram_sim.py) ever sets it |
| [`hyperram_dqs_phy.py`](../gateware/soc/peripherals/hyperram_dqs_phy.py) 4:1 gearing | ODDRX2DQA / IDDRX2DQA / TSHX2DQA | no | **R1** — no clock enable on any of them; 32 bits move per `sync` cycle unconditionally |
| …DDRDLL settle FSM `:277-300` | 5 states, three 8-cycle holds | no | **R2** — must complete before any transaction. But `with m.If(lock)` at `:280` has **no timeout and no retry**, so a DLL that never locks hangs `dll_ready` for the life of the bitstream |
| …`ReadClkSelWindow` `:162-193` | PAUSE 4T either side of a tap change | no | **R1** — a CSR write is asynchronous to `hr` and cannot bracket itself; `tap.idle` is a HyperBus-cycle-accurate condition. The tap *value* is already a CSR |
| …`DEL_MODE` strings, `DDRDLL_SETTLE_CYCLES`, `READCLKSEL_PAUSE_CYCLES` | delay-line mode and hold widths | **param** | `:73`, `:84`, `:98`. The DQ delay is `DELAYG` — a *fixed* delay with no move/load ports, so a runtime delay axis needs a different primitive, not a wire |
| …`sel_applied`, `sel_settling` | — | **yes, read-only** | driven at `:345-346`, read by nothing anywhere. So no master can honour the "do not issue while the tap is moving" rule the file states at `:233-235` |
| [`bootram.py`](../gateware/soc/bootram.py) `HyperRAMWishbone` datapath | beat/word assembly, RMW merge | no | **R1**, inherited from the controller above |
| …arbiter `STARTING` state `:742-748` | 1-cycle race | no | **R1** — `idle` is asserted combinationally in the cycle `start_transfer` is issued, so an FSM watching for its return falls straight through. Solved twice, independently, the second time in `hr` at `hyperram_share.py:410-414` |
| …arbiter priority `:704-727` | JTAG > Wishbone > CSR | no | **R1** — a JTAG shift clocks off TCK, which the FPGA does not control. No anti-starvation: a Wishbone master requesting every cycle starves the staging port, unreachable only by boot ordering |
| …`max_burst_words` / `max_stall` | tCSM and tCK caps | **param** | `:266-268`, `:611`. **Both are pruned in the shipping build**: `sustained=False` makes `burst_candidate` `C(0)`, so `bus.cti`/`bus.bte` are advertised and read by nothing, and every cache-line fill is N single beats |
| …`read_stall_cycles` `:673` | — | **yes** | its own comment calls it "a runtime selector like READCLKSEL"; nothing drives it, so the sweep it exists for needs a rebuild |
| …`clk_stop` `:619` | — | **yes, read-only** | driven, unreadable — and now **unconnectable**: the PHY it gated moved to `hyperram_share.py`, which has no stall input. `clock_stop=True` today builds stall logic that gates no CK, and nothing refuses it |
| …`stall_timeout` `:785` | — | dead three ways | `want_stall` is `C(0)`; its consumer `psram.final_word` is a handover field read by nothing (`hyperram_share.py:265` vs `:386`); no CSR exposes it |
| [`hyperram_probe.py`](../gateware/soc/peripherals/hyperram_probe.py) | nothing — no constructor parameters | — | **the exemplar.** `sel` is a 7-bit write register (`:206-209`); firmware sweeps taps in milliseconds from one bitstream |
| …`sel` bits 6 and 5:4 | — | **yes** | `top.py:1352` synchronises `sel[:4]` only. Bit 6 (REGISTER SPACE) and bits 5:4 (read-stall) leave the probe and terminate. Ranks 4 and 5 |
| [`hyperram_ck.py`](../gateware/soc/peripherals/hyperram_ck.py) | the rung table `ck_rungs` | **already runtime** | the *set* of rungs is per-bitstream (PLL output dividers are `p_` configuration bits); **which rung is live is a CSR write** through `DCSC` ([`hyperram_clocks.py:400-425`](../gateware/soc/hyperram_clocks.py)) |
| [`hyperram_bist.py`](../gateware/soc/peripherals/hyperram_bist.py), [`bist_csr.py`](../gateware/soc/peripherals/bist_csr.py) | `addr_width=7` → 64 registers | no | the mechanism the whole argument depends on. `addr_width=8` cost 1,024 flops (`bist_csr.py:130-136`) |

### Flash

| peripheral | decided at elaboration | CPU's? | reason / what it needs |
|---|---|---|---|
| [`flash.py`](../gateware/soc/peripherals/flash.py) mmap FSM `:147-257` | Wishbone slave sequence | no | **R5** — `:255` drives `bus.ack` for a cycle the CPU is stalled in, and this window is also the I-cache refill path for `.text` |
| …`READ_MODES` `:85-97` | opcode, lane widths, dummy bits | **param** | `:199-202` and `:212` are **Python** `if`s on a Python `int`: in `"single"` the `DUMMY` and `DUMMY-RET` states **do not exist**. The states must be built unconditionally before the mode can be a register |
| …`divisor` `:396` | SCK rate | **param, but not free** | three netlist effects: the clock generator's counter width, `:526`'s guard folding to constant 1, and `:528` `sr_in_shift.eq(divisor == 0)` folding to a constant. **Divisor 0 and divisor 1 are different circuits**, not one circuit with a different count |
| …`MMAP_DEFAULT_TIMEOUT = 256` `:144` | 4.27 µs chip-select hold | **param** | a bet on CPU locality, with no readback |
| …`cs_delay = 0` `:433` | tCSS setup | **param** | a hardcoded local, so the `WaitTimer` branch at `:435-441` is dead in every build |
| …`byteorder` `:247-248`, `hasattr(pads.cs, "oe")` `:458-459` | two more Python conditionals | note only | the `hasattr` is never taken — `PinSignature`'s `cs` has no `oe` — and would change the design silently if the resource grew one |
| …crossbar `locked` `:314` | — | **yes, read-only** | genuinely unobservable. `grant` **does** reach a CSR (`top.py:1243`), into the dead readout at rank 9 |
| …`HoldableSPIController` CS hold | already a CSR at `:614` | — | **R4 for the residue** — the CPU issues CSR writes far slower than the PHY drains the TX FIFO, so software cannot keep `tx_fifo.r_rdy` asserted; only the `held | inner.cs` OR is gateware |
| …the inner controller's `cs` CSR at `0x04` | — | delete | written by nothing. [`flash.rs:17`](../firmware/cynthion-soc/src/flash.rs) says so: "a write PULSE; cannot hold chip select. Unused here" |
| [`flash_cdc.py`](../gateware/soc/peripherals/flash_cdc.py) | `depth=4`, `phy_domain` | no | **R6.** Zero CSRs, and now zero instances: `FLASH_PHY_FAST = False`. `depth` is a sizing bet, not a timing case |
| [`flash_sck_full.py`](../gateware/soc/peripherals/flash_sck_full.py) | `gated_sck` | no | **R1** — one SCK period per domain cycle, through the single LUT that is the only legal path to `USRMCLKI`. Not built (`FLASH_FULL_SCK = False`). `:46-48` makes `FLASH_FULL_SCK=1` **plus** `FLASH_DIVISOR>0` a build-time crash, which `top.py` does not mention |
| [`block_ram.py`](../gateware/soc/peripherals/block_ram.py) | `size` → EBR count | no | **R5** — `:125` acks a cycle the CPU is stalled in, for the memory holding its own stack. `init` is the one input that needs no resynthesis ([`bram_patch.py`](../scripts/bram_patch.py)) |
| …`inside` `:102-103` | bounds mask | note | constant 1 at `RAM_SIZE = 32 KiB`. The module exists to allow a non-power-of-two size and the shipping size is a power of two; `top.py:150-151` still states the retired constraint |

### Serial, I2C, and the board

| peripheral | decided at elaboration | CPU's? | reason / what it needs |
|---|---|---|---|
| [`i2c_master.py`](../gateware/soc/peripherals/i2c_master.py) bit engine | 200 ns slots, 5 per bit | no | **R1/R4** — a CSR peripheral answers in 5 cycles *before* the LSU and uncached `iobus` round trip ([`bus/fault.py:88`](../gateware/soc/bus/fault.py)), two pins need two stores, and an SDA edge while SCL is high is a START to every device on the segment |
| …**PRER** | nothing — a plain RW register | — | **the model.** `prescale_for()` is exported from the gateware (`:192-200`), called by [`soc_generate_pac.py:649-661`](../scripts/soc_generate_pac.py) with the *solved* clock, and re-derived at run time from `clock::measured()` ([`bus.rs:186-190`](../firmware/cynthion-soc/src/bus.rs)). [`i2c_rate_sweep.py`](../scripts/i2c_rate_sweep.py) walks the whole ladder from **one bitstream** |
| …`SLOTS_BIT`, `SLOTS_COND` | t_LOW:t_HIGH split | **param** | `:178-181`. A driver can compute f_SCL and cannot learn the split |
| [`i2c_mux.py`](../gateware/soc/peripherals/i2c_mux.py) select hold | move only at `idle` | no | **R1** — the window is opened by a CSR write firmware has *already issued*: the write completes in ~5 cycles, the transfer it must not disturb is 540 |
| …`applied` `:205-208` | — | **yes, read-only** | driven and read, but `_select` is `csr.action.RW`, so firmware reads its own request back, never the pins. Both FUSB302Bs answer 0x22 with the same identity byte, which is exactly where a wrong-bus read looks plausible |
| …`BUS_RESOURCES` `:75-79` | — | delete or use | in `__all__`, imported by nothing; `top.py:2017-2019` and `:2041-2043` restate the same mapping twice more |
| [`uart16550.py`](../gateware/soc/peripherals/uart16550.py) register map | 16550A layout | no | not a timing case at all — it exists so Linux's `autoconfig` passes (`:531-538`) |
| …`thre_int` `:487-497` | edge-latched transmit-empty | no | **R3** — derived from level `LSR.THRE`, the standard handler sequence is an interrupt storm caused by firmware behaving correctly |
| …`FIFO_DEPTH = 16` `:162` | both FIFOs | **param** | IIR[7:6] only says "a 16550A"; the depth is inferred from a part number |
| …DLL/DLM `:315-320` | — | **wire it** | rank 7 |
| …FCR bits 7:6 | receive trigger level | note | silently discarded; with IIR id `0b110` never raised, a Linux 8250 gets neither a trigger level nor a character timeout |
| [`serial_line.py`](../gateware/soc/peripherals/serial_line.py) bit engine | 8.68 µs per bit | no | **R1** — `tx_oe` is *counted*, not inferred: at divisor 8 `phy.tx.rdy` rises at cycle 80 while the stop bit occupies 80..87, so the line is undriven for the whole stop bit (`:56-62`) |
| …idle qualifier `:212-228` | 12 bit periods | no | **R1** — it gates `phy.rx.i` every `sync` cycle so the PHY's FSM cannot leave IDLE. Filtering *bytes* instead leaves the receiver resynchronising its bit phase to noise |
| …`divisor` port | reset value only | **yes — already a port** | rank 6 |
| …`armed`, `frame_errors` `:183-184` | — | **yes, read-only** | both documented "Diagnostic:", both driven, and the only reader in the repository is [`soc_serial_sim.py`](../scripts/soc_serial_sim.py). So the failure the module documents as its known trade — the receiver disarms and a back-to-back sender never gives it 12 idle bits — is not observable by any means the product has |
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
| …`SYNC_CEILING_MHZ` `:112`, `HYPERRAM_CK_CEILING_MHZ` `top.py:769` | nothing | — | pure refusal gates, deliberately outside `VARIANT_ENV` because they change no netlist. The right shape: fail in 0 s rather than 200 s into nextpnr |
| [`ulpi_window.py`](../gateware/soc/peripherals/ulpi_window.py) CSR reset sequencer `:391-409` | the same two counts | **not a duplicate** | its FSM is `domain="usb"`, and `usb` is in reset until `phy_ready` — so it **physically cannot start before the POR finishes**. No ordering to get wrong. It is also narrower on purpose: TARGET's pad only (AUX carries the console), the `ULPIRegisterWindow` submodule only, and it is *reportable* through `STATUS.resetting`. The narrowing is what firmware would have to re-implement, not the 1.2 ms wait |
| …`TIMEOUT_CYCLES = 4096` `:125` | 68.27 µs | **param** | ~400× headroom over a register read's "under ten cycles". Transcribed by hand into [`ulpi.rs:66,78`](../firmware/cynthion-soc/src/ulpi.rs) as `SETTLE_US = 86` and `RESET_US = 1_503`; the sim checks the gateware constants against the datasheet and nothing checks the firmware bounds |
| [`vbus_csr.py`](../gateware/soc/peripherals/vbus_csr.py) reset state | all four switches open | no | **R2**, and it is the cleanest instance. `enable` is `csr.action.RW` with `init=0`; the gate is **combinational** (`:128-133`), not a latched copy; the pads are active-high `Pins`, so 0 is open with no inversion to get wrong. The state exists between configuration and the first store instruction — there is no instruction stream in that interval |
| [`sideband_csr.py`](../gateware/soc/peripherals/sideband_csr.py) | bit positions only | — | the model for the rest: no baked timing at all |
| [`fabric_status.py`](../gateware/soc/peripherals/fabric_status.py) | `DTR_PERIOD_BITS = 19` `:110` | **moves** | a free-running 19-bit counter, **8.738 ms** at 60 MHz, against a conversion that takes 8 cycles. A write-to-start bit plus the `valid` readback that already exists at bit 7 replaces it. The cycle count is baked and its wall-clock meaning floats with `SYNC_MHZ`: 10.49 ms at 50 |
| …the build's own configuration | **deliberately absent** | correct, and incomplete | `:16-30` refuses to put constants in fabric — *"folded logic that moves with its value"* — and routes identity to USERCODE, clocks and cache to the build record. Correct in principle; see rank 8 and the correction below for where the routing does not arrive |

### CPU, clocks and the build itself

| peripheral | decided at elaboration | CPU's? | reason / what it needs |
|---|---|---|---|
| [`cpu/intc.py`](../gateware/soc/cpu/intc.py) trigger per source | level vs edge | no | **R3** — `trigger=` is a constructor argument to `amaranth_soc.event.Source`; it decides which flops exist. There is no mux and no register. The table and its per-device justification: [`soc-interrupts.md`](soc-interrupts.md) |
| …8 of 18 lines | tied low | by design | source number = bit position, so wiring one up renumbers nothing. Each still costs a pending/enable pair |
| [`cpu/cpu.py`](../gateware/soc/cpu/cpu.py) cache geometry | `CACHE_SETS`, `CACHE_WAYS` | no | genuinely per-bitstream — SpinalHDL generation, then synthesis. [`soc_cache_sweep.py`](../scripts/soc_cache_sweep.py) is the right shape for it |
| …performance counters | count only | note | the plugin arrives with `--with-rdtime` regardless, so dropping the flag generates a **byte-identical** core; the count is the only free variable |
| …two unconnected core outputs | `ndmreset`, `debug_stoptime` | **bug** | an openocd `ndmreset` reaches nothing, and `mtime` keeps advancing while the hart is halted, so `mtimecmp` deadlines pile up across a breakpoint and the tick storms on resume |
| [`cpu/clint.py`](../gateware/soc/cpu/clint.py) | `mtime` compare | no | RISC-V standard; nothing dead |
| [`variant.py`](../gateware/soc/variant.py) | 3 env axes | — | `CYNTHION_HYPERRAM_CK_MHZ`, `CYNTHION_HYPERRAM_DQS`, `CYNTHION_SYNC_MHZ`, each hashed into the slug |
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

* `register_space` ← `bootram.py:930` `.eq(0)` — the staging path only
* `single_page` ← `bootram.py:922` and every other synthesizable caller
* `fabric_reconfigured` ← `top.py:1809` — deliberate, and `top.py:1804-1805`
  says why: nothing in this design has ever had anything to report

**Driven, read by nothing in the SoC** — a diagnostic that cannot report:

`armed`, `frame_errors` (`serial_line.py`) · `timed_out`, `latency_below_trwr`,
`register_active` (`hyperram_controller.py`) · `sel_applied`, `sel_settling`
(`hyperram_dqs_phy.py`) · `phy_ready` (`clocks.py`) · `clk_stop`,
`bursting_out` (`bootram.py`) · `own` (`sideband_csr.py`) · the nine `o_*` in
`flash.py`

`phy_ready` is the one with a named consequence. `sync` is released when the PLL
locks — tens of µs — while `usb` stays in reset for 1.20213 ms. A ULPI access in
that window leaves `busy` set, [`ulpi.rs:66`](../firmware/cynthion-soc/src/ulpi.rs)
bounds the wait at 86 µs, and returns `Error::NoPeripheral`, whose documented
meaning is *"a bitstream/firmware mismatch"*. So "the PHY is still preparing" is
reported as "the wrong bitstream", and the one bit that distinguishes them is
invisible. `usb3343_init` is on that path.

`own` also shows why the checker that was supposed to catch this class does not:
[`check_ports_connected.py:63-75`](../scripts/check_ports_connected.py) searches
a haystack that includes the declaring file, so `self.own.eq(own)` inside the
peripheral's own `elaborate` satisfies it. **Any output driven inside its own
module passes.** The two checkers disagree and the weaker one is the one on the
gate.

**Nothing drives it, anywhere** — an input at its `init` for the life of the
bitstream:

`recovery_cycles`, `burst_cycles`, `burst_beats`, `min_latency_clocks`,
`extra_latency` (both controllers) · `read_stall_cycles` (`bootram.py`) ·
`grant` (`flash.py`)

**Found by hand, outside the scan's reach.** A mux that forwards a field counts
as a driver, so a chain ending at an undriven face reads as wired:
`stage.latency_clocks`, `stage.low_latency_clocks`, `stage.fixed_latency` are
muxed into the controller at `hyperram_share.py:191-193` and **neither face is
driven by anything**. Same for `bist.read_phase`, so the half-cycle read window
is forced to 0 in BIST mode while being live on the staging side — an asymmetry
between two masters that nothing declares.

**The sharpest one is not a port at all.** The DQS PHY never drives
`phy.rwds.i`: its only RWDS input path is `BB → rwds_in → DQSBUFM.i_DQSI`
([`hyperram_dqs_phy.py:307-309`](../gateware/soc/peripherals/hyperram_dqs_phy.py),
`:388`). [`hyperram_dqs_controller.py:359-360`](../gateware/soc/peripherals/hyperram_dqs_controller.py)
reads it. So on hardware `rwds_asks` is structurally 0, `extra_latency` can
never set, and the variable-latency election is exercisable only against the
behavioural PHY in `dqs_config_tb.sv`. The non-DQS path is fine — luna's
`HyperRAMPHY` drives it from `IDDRX1F`.

## Switched off, with a reason

Not the same as the list below. Each of these is built code that a recorded
decision keeps out of the netlist, which is an elaboration decision like any
other row above.

| module | why | where the reason is |
|---|---|---|
| [`flash_cdc.py`](../gateware/soc/peripherals/flash_cdc.py) | `FLASH_PHY_FAST = False`, not env-overridable | `top.py:676-703`: at `sync` 60 the only integer ratios are 60 (no change) and 120 (does not close at 111.26 MHz). ODDR first, then this |
| [`flash_sck_full.py`](../gateware/soc/peripherals/flash_sck_full.py) | `FLASH_FULL_SCK = False` | `top.py:705-710`. Also `:46-48` makes `FLASH_FULL_SCK=1` **plus** `FLASH_DIVISOR>0` a build-time crash, which `top.py` does not mention |
| `FlashILA` | removed from the SoC in `620901b` | still assumed built by [`soc-size-review.md:136-147`](soc-size-review.md) and [`soc_instrumentation_cost.py:96-106`](../scripts/soc_instrumentation_cost.py), so the peripheral-area figures there are an over-estimate |

## Merged, in no layer yet

A different question from the table, and not answerable by moving anything: this
is code with no caller. Listed so it is not mistaken for a misplaced decision.

* **`ClockStopPHY`** ([`bootram.py:157-202`](../gateware/soc/bootram.py)) —
  instantiated nowhere in `gateware/`, only in [`soc_hyperram_sim.py`](../scripts/soc_hyperram_sim.py).
  It is the Active Clock Stop path, i.e. the only legal way to stall a HyperBus
  transaction, so its absence is why `sustained=False`
* **`solve_dcsc_rungs`** ([`hyperram_clocks.py:185-235`](../gateware/soc/hyperram_clocks.py)) —
  one test caller, no production caller. Superseded by `solve_hr_pll_rungs` +
  `HyperRAMDomains`, and its `outputs=4` default is unreachable now that
  `MAX_RUNGS = 2`
* **`PulseCross`** ([`bist_csr.py:55-80`](../gateware/soc/peripherals/bist_csr.py)) —
  exported, never instantiated; [`soc_bist_cdc_sim.py:73`](../scripts/soc_bist_cdc_sim.py)
  defines its own copy
* **`BUS_RESOURCES`** ([`i2c_mux.py:75-79`](../gateware/soc/peripherals/i2c_mux.py)) —
  in `__all__`, imported by nothing; `top.py:2017-2019` and `:2041-2043` restate
  the same mapping twice more
* **`HyperRAMBist.transport`**, **`StreamBuffer.depth`**, **`BlockRAM.size`** and
  its `init` setter — properties with no reader. [`soc_stream_buffer_sim.py:77`](../scripts/soc_stream_buffer_sim.py)
  re-declares its own `DEPTH` rather than reading the one that exists

SBU and SWD are the largest instance of this and are **out of scope here** —
they belong to [#518](https://github.com/awtoau/cynthion-workspace/issues/518),
not to this audit.

Two dangling references found on the way:
`scripts/riscv_flash_crossbar_sim.py` and `scripts/riscv_flash_ila_decode.py`
are cited five times — `flash.py:283`, `flash.py:560`, `top.py:1172`,
[`board/core.py:17`](../gateware/board/core.py) and
[`upstream-boundary.md`](upstream-boundary.md) — and **neither file exists**.

## Corrections

Where the framing this audit was given turned out to be wrong.

| claim | correction |
|---|---|
| [#531](https://github.com/awtoau/cynthion-workspace/issues/531): `register_space` is "driven by nothing" | it is driven by a **literal 0** on the staging path (`bootram.py:930`, with a comment arguing for it) and driven **live** by the BIST engine (`hyperram_ceiling_top.py:820`). Register access already exists; what is missing is register access from the CPU's memory window. Firmware already writes CR0/CR1 through the BIST path ([`bist.rs`](../firmware/cynthion-soc/src/bist.rs)) |
| [#531](https://github.com/awtoau/cynthion-workspace/issues/531): `FLASH_DIVISOR` is the type case | it is the weakest of the ranked items, on its own evidence. `top.py:599-603` records the sweep as **already done and all-pass** — 60 points, five modes × four divisors × three sync rates, up to 144 MHz SCK, nothing failed. The binding limit is that SCK derives from `sync`, not the divisor. And the change is not free: divisor 0 and divisor >0 are **different netlists** (`flash.py:526`, `:528`), so a CSR must build the divisor>0 circuit and give up divisor 0's extra shift beat |
| [#531](https://github.com/awtoau/cynthion-workspace/issues/531): "24 modules" | 24 files, one of which is `__init__.py`. Two more are switched off by a recorded decision, and two are merged-but-unwired and out of scope, so the table covers 19 |
| [#327](https://github.com/awtoau/cynthion-workspace/issues/327) / [#305](https://github.com/awtoau/cynthion-workspace/issues/305): `control_vbus_in_en`/`aux_vbus_in_en` are `Out()` ports reaching no pad | **fixed.** `VbusControl` has exactly four outputs (`vbus_csr.py:111-114`) and all four reach pads (`top.py:1889-1896`); the post-mortem is at `vbus_csr.py:15-22` and the matching firmware note at [`vbus.rs:129-131`](../firmware/cynthion-soc/src/vbus.rs). The stale artefact is a doc: [`chips/ecp5/pin-usage.md:141-142,186-190,455`](chips/ecp5/pin-usage.md) still calls it "the worst finding here" |
| [#323](https://github.com/awtoau/cynthion-workspace/issues/323): `gateware_id.py:243-249` | **the file does not exist** — replaced by `fabric_status.py` in `8a915b3`. The DTR finding survives verbatim at `fabric_status.py:110,175-191`; the "`GatewareId` reports `sync_hz`, `usb_hz` and the cache geometry" claim is now understated, because **no** fabric register reports any of them |
| [#531](https://github.com/awtoau/cynthion-workspace/issues/531): four reasons | R5 and R6 above are neither of the four, and between them account for most of the `stays` rows |
| [#531](https://github.com/awtoau/cynthion-workspace/issues/531): the `write_ready` counter-case | **confirmed exactly**, and it is the one row that needed no rewording |
| [#323](https://github.com/awtoau/cynthion-workspace/issues/323): "no `DCSC` primitive is instantiated" | wrong — `hyperram_clocks.py:400-425`, guarded by `len(clk_rung) > 1` |
| [#323](https://github.com/awtoau/cynthion-workspace/issues/323): "CK itself genuinely is per-bitstream" | stale — the rung *set* is; which rung is live is a CSR write |
| [#323](https://github.com/awtoau/cynthion-workspace/issues/323): `solve_dcsc_rungs` "has no caller anywhere" | one test caller. The conclusion stands — nothing plans a build with it — but its `outputs=4` is unreachable now that `MAX_RUNGS = 2`, so the "31 bitstreams → 8" row overstates the win by 2× |
| [#323](https://github.com/awtoau/cynthion-workspace/issues/323): `FlashILA.SIGNAL_NAMES` is a per-bitstream axis | moot — `FlashILA` is not built |
| [#323](https://github.com/awtoau/cynthion-workspace/issues/323): "`grant` and `locked` have no readback" | half stale — `grant` has one, into a CSR no firmware reads; `locked` has none |
| [#323](https://github.com/awtoau/cynthion-workspace/issues/323): HyperRAM `RESET#` tied at `bootram.py:706,723` | those lines no longer exist. The tie moved to `hyperram_share.py:388` on the staging side; the BIST side drives it, so firmware can pulse RESET# indirectly by claiming BIST mode. [`hyperram.rs:257-268`](../firmware/cynthion-soc/src/hyperram.rs) argues the staging path should *not* have the bit, because array contents must survive a CPU reset |
| [#341](https://github.com/awtoau/cynthion-workspace/issues/341) | **the gateware half has landed.** Seven ports exist with reset = the old constant, exactly as specified. What is missing is the CSR half, and two of the ports are too narrow to move (ranks 2 and 3) |

Line numbers in [#323](https://github.com/awtoau/cynthion-workspace/issues/323)
have drifted throughout — `hyperram_controller.py` by ~130 lines,
`hyperram_clocks.py` by ~70, `bootram.py` past EOF in two places, and one file
it cites is gone. The substance mostly holds; the references do not.

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
| [#532](https://github.com/awtoau/cynthion-workspace/issues/532) | ranks 1-3 — the seven timing ports, and the two that are too narrow to move |
| [#533](https://github.com/awtoau/cynthion-workspace/issues/533) | ranks 4, 5, 9, 10 — the shell columns that cannot move, and instrumentation whose reader was deleted |
| [#534](https://github.com/awtoau/cynthion-workspace/issues/534) | the DQS PHY never drives `phy.rwds.i` |
| [#535](https://github.com/awtoau/cynthion-workspace/issues/535) | rank 8 — provenance, and `VEXII_ROOT` not hashed |
| [#536](https://github.com/awtoau/cynthion-workspace/issues/536) | ranks 6-7 — the console baud, and `armed`/`frame_errors` |
| [#537](https://github.com/awtoau/cynthion-workspace/issues/537) | the port checker that cannot fail, and the dangling-port inventory |
| [#538](https://github.com/awtoau/cynthion-workspace/issues/538) | four defects found on the way, none of them layering questions |
| [#539](https://github.com/awtoau/cynthion-workspace/issues/539) | the stale references |

## Row to issue

| issue | rows it corresponds to |
|---|---|
| [#323](https://github.com/awtoau/cynthion-workspace/issues/323) | the whole table — this is its sweep, re-derived rather than copied |
| [#341](https://github.com/awtoau/cynthion-workspace/issues/341) | ranks 1-3; the controller's seven lever rows |
| [#315](https://github.com/awtoau/cynthion-workspace/issues/315) | the `stays`/**R2** rows are its boundary: `stream_buffer` flush, the DDRDLL settle, the ULPI POR |
| [#319](https://github.com/awtoau/cynthion-workspace/issues/319) | the `register_space` row, corrected above |
| [#311](https://github.com/awtoau/cynthion-workspace/issues/311) | pin drive and slew — outside this table, because they are bitstream *configuration bits*, patchable without resynthesis, not fabric registers |
| [#327](https://github.com/awtoau/cynthion-workspace/issues/327) | the "tied to a literal" class, one level up: [#327](https://github.com/awtoau/cynthion-workspace/issues/327) is about pads, this is the same shape at a module boundary |
