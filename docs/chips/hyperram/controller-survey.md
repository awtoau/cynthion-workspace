# HyperRAM controller survey, 2026-08-10

Nineteen open-source HyperBus controllers read as source. What they do, what is
worth taking, and what to avoid.

**Scope.** This is the *external* comparison. Our own faults are
[`2026-08-10-audit.md`](2026-08-10-audit.md); the part is
[`w956a8.md`](w956a8.md); the method is [`bist-plan.md`](bist-plan.md).

**Surveyed 2026-08-10.** Commit and date are recorded per row, so a stale row is
visible rather than assumed. Sources were cloned to `tmp/hyperram-survey/`
(untracked, regenerable — the citations below are what survives).

## The comparison

Ranked by how much of the protocol each one actually implements.

| implementation | commit / date | lang | licence | tCSM chop | read watchdog | CR0 from RTL | register read | RWDS during CA | usable on Amaranth 0.5 + ECP5 |
|---|---|---|---|---|---|---|---|---|---|
| `pulp-platform/hyperbus` | `e21a9ce` 2026-07-22 | SystemVerilog | SHL-0.51 | **yes** | **yes, same counter** | via CSR | yes | sampled, latched at end of CA | no — SV |
| `fpga-professional-association/hyperram` | `e4cc929` 2026-07-11 | SystemVerilog | — | yes | **yes, best documented** | yes | yes | sampled | no — SV |
| `OVGN/OpenHBMC` | `fc1c154` 2022-10-07 | Verilog | MIT | **yes** | no | **CR0 + CR1** | yes | sampled | no — `ISERDESE2`/`IDELAYE2`, Xilinx 7-series only |
| `LatticeSemi/hyperram_mc` | `2250259` 2025-12-19 | Verilog | Lattice | no | **N/A — open-loop capture** | **CR0 + CR1** | yes | sanity-checked only | no — Lattice-licensed, Nexus primitives |
| `embelon/wb_hyperram` | `186838b` 2023-12-09 | SystemVerilog | Apache-2.0 | via `trmax` | **yes, + status bit** | no | yes | sampled | no — SV |
| LiteX `soc/cores/hyperbus.py` | `f23babc` 2026-06-19 | Migen | BSD-2 | no | **no** | BIOS, not RTL | yes | sampled, real mode switch | no — Migen |
| `ChipFlow/chipflow-digital-ip` | `bb17e25` 2025-12-18 | **Amaranth** | BSD-2 | no | **N/A — fully counted** | no (`access="w"`) | **none** | sampled mid-CA | partly — no DDR primitives, SDR capture |
| `MJoergen/HyperRAM` | `adeb1b6` 2024-10-22 | VHDL | — | no | **flag only — see the trap** | yes | yes | sampled | no — VHDL |
| `micro-FPGA/OpenMBMC` | `b8112f6` 2026-06-11 | Verilog | MIT | inherited | no | yes | yes | sampled | no |
| `litex-hub/litehyperbus` `hyperram_ddrx2` | `76454e4` 2022-07-06 | Migen | BSD-2 | no | **yes, 20 cycles** | no | no | not sampled | no — Migen |
| **LUNA `psram.py` (ours, forked)** | 0.2.3 == main | Amaranth | BSD-3 | **no** | **no** | no | yes | sampled then discarded | it is what we run |
| `no2fpga/no2hyperbus` | `909dfab` 2021-10-20 | Verilog | CERN-OHL-P | no | no | no | partial | no | no |
| `zeldin/litehyperram` | `624e560` 2023-10-07 | Migen | BSD-2 | no | no | no | no | no | no |
| `Wren6991/HyperRam` | `fd5b310` 2019-02-10 | Verilog | — | no | no | no | no | no | no |
| `wtfuzz/hyperbus` | `570a7cb` 2020-12-23 | Verilog | — | no | no | no | no | **drives RWDS on register write — wrong** | no |
| `ZipCPU/wbhyperram` | `c881c50` 2018-11-13 | Verilog | GPL-3.0 | no | no | no | partial | no | no — GPL |
| `blackmesalabs/hyperram` | `70b4648` 2018-08-10 | Verilog | — | no | no | no | no | **drives RWDS on register write — wrong** | no |
| `gtjennings1/HyperBUS` | `37bf73d` 2019-01-08 | Verilog | — | no | no | no | no | no | no |
| `gregdavill/litex-hyperram` | `9b17913` 2019-12-06 | Migen | none stated | no | no | no | no | no | no — unlicensed, 7 years stale |
| `pulp-platform/udma_hyperbus` | `78613ab` 2019-01-23 | SystemVerilog | SHL | no | no | no | yes | sampled | no — superseded by the row above |
| `zf3/psram-tang-nano-9k` | `394ae14` 2022-10-11 | Verilog | — | no | no | no | no | no | no — Gowin |
| `XarkLabs/Xosera` | — | Verilog | MIT | — | — | — | — | — | no HyperRAM controller; uses external IP |

**Nobody calibrates the read window.** Not one implementation sweeps a capture
phase or centres in the eye. Lattice ties its `DPCR` delay registers to zero;
litehyperbus exposes `DELAYF` move ports with no sweep FSM driving them; LiteX
offers only a build-time `dq_i_cd` domain choice. Our `readclksel` sweep is
ahead of all of them. The missing piece is a bitslip, and that is LiteDRAM's
(#186), not any HyperRAM core's. Answers the survey half of #244.

**Non-GitHub hosting is empty.** GitLab, Codeberg, sr.ht, Bitbucket,
SourceForge, OpenCores and grep.app returned no HyperRAM/HyperBus RTL not
already listed. Recorded so nobody repeats the search.

## The three rules

### 1. Never let a device-sourced strobe be the only exit

A state whose exit depends on RWDS, DATAVALID or any other device output hangs
for ever when the device stops. Count the beats you asked for; use the device's
strobe to *validate* alignment, not to terminate.

Lattice is the strongest form: `lat_hyperbus_controller.v` writes the read FIFO
from an open-loop counter and never gates capture on RWDS at all, then compares
`rwds_i_phy2c != 4'b0101` into a sticky `csr_rsamperr` (`:46`, `:445`,
`:556-557`). Structurally unhangable, with the strobe kept as evidence.

ChipFlow reaches the same property differently — every state exit in
`_hyperram.py` is a counter compare, so no device signal appears in the exit
path at all.

### 2. One counter, both jobs

tCSM chopping and the read watchdog are the same timer. Arm it *before*
entering the data phase, and let expiry take the ordinary teardown rather than
a special error state.

### 3. Export `fsm.state`

Neither of our controllers nor upstream LUNA does. A stuck controller is
currently unobservable from outside — the ceiling rig could see `idle=0` and
nothing more. One line each.

## Code to borrow, with lines

**`pulp-platform/hyperbus`, `src/hyperbus_phy.sv` (`e21a9ce`)** — arm before
entry, expire into the normal path:

- `:308`, `:328`, `:358` — `timer_d = cfg_i.t_burst_max` set on *every* edge
  into `Read`/`Write`, before the data phase starts
- `:383-386` (`Read`) and `:405-408` (`Write`) — `// Force-terminate access on
  burst time limit`, `if (ctl_timer_one)` → `timer_d = cfg_i.t_csh_cycles;
  state_d = WaitXfer` — the same teardown a normal completion uses
- `hyperbus_pkg.sv:72` — `t_burst_max: ((MinFreqMhz*35)/10)`, commented
  *"t_{csm}: At lowest legal clock (100 MHz) 3.5us (0.5us safety margin)"* —
  derived from tCSM with its margin stated, not a round number
- `:322` — `trx_rwds_sample_ena = ~ctl_write_zero_lat & (timer_q > 2)`, and
  `:323-324` latches the decision at the end of CA so a later RWDS change
  cannot erase it
- `:335-338` — RWDS output enable is `~ctl_write_zero_lat`, citing the spec page

**`embelon/wb_hyperram`, `src/hyperram.sv` (`186838b`)** — the sizing and the
status bit:

- `:221` — `cycle_cnt_r <= {trmax_r, 1'b0} - 2;  // setup timeout for read`
- `:239-246` — `S_READ` leaves to `S_POST` on *either* the beat count or the
  timeout, and `read_timeout_r <= (cycle_cnt_r == 0) && (read_cnt_r != 0)`
  distinguishes which
- `:46` `MIN_TIMEOUT = 4` enforced at `:174`, formally asserted at `:861`,
  `:871-874`; `:8` `DEFAULT_TIMEOUT = 20`
- `:205` — register-space writes load `cycle_cnt_r <= 1` (2 bytes), a real
  zero-latency special case rather than a shared path
- exported as a CSR at `src/wb_hyperram.sv:207`

**`LatticeSemi/hyperram_mc`, `lat_hyperbus_controller.v` (`2250259`)** — RWDS as
sanity check, and CR0/CR1 from RTL:

- `:556-557` — `if (rwds_i_phy2c != 4'b0101) csr_rsamperr <= 1'b1;`
- `:210` `ST_CTRL_1_POWERUP` → `:292` `ST_CTRL_2_SET_CR0` → `:306`
  `ST_CTRL_3_SET_CR1`, unconditional, before any user traffic
- `:236` — `CR0_WRITE = {8'h60, 8'h00, 8'h01, 8'h00, 8'h00, 8'h00}`, byte for
  byte the datasheet's §9.1 Table 5 entry; `:235` `CR0_READ` uses `8'hE0`
- `:252`, `:300`, `:312`, `:496` — a `no_latency` flag carried through the FSM
  rather than a branch duplicated per state

**`fpga-professional-association/hyperram`, `rtl/hyperbus_ctrl.sv` (`e4cc929`)**
— the best-documented watchdog:

- `:322` — `localparam int unsigned READ_STALL_LIMIT = 32;  // RWDS Low >= 32
  clk => timeout`
- `:1358-1368` — at `READ_STALL_LIMIT - 1`, raise CS# via `ST_RD_ABORT` and
  `err_timeout <= ~int_read` — an internal housekeeping read never surfaces a
  user-visible timeout
- `:560` — holds `stall_cnt` while CK is stopped, so a *deliberate* Active
  Clock Stop is never mistaken for the error stall. We have clock stop
  (`bootram.py`, `ClockStopPHY`); this distinction is one we will need
- `:227`, `:378` — the counter and its output are declared at the top with the
  spec section that justifies the number

**LiteX `soc/cores/hyperbus.py` (`f23babc`)** — register CA and CS# high:

- `:830-832` — `cmd[47].eq(~reg.we)`, `cmd[46].eq(1)`, `cmd[45].eq(1)  # Burst
  Type (Linear)` — CA[45] hardcoded, not inherited from a caller
- `:841-843` — memory CA hardcodes `cmd[45].eq(1)` too
- `:718-724` — `cs_high_cycles=9` as a constructor argument, `if cs_high_cycles
  < 1: raise ValueError`
- `:1044-1046` — a dedicated `END` state, `If(cycles == (cs_high_cycles - 1))`;
  **every** path including `REG-WRITE` (`:923`) and `REG-READ` (`:934`) exits
  through it
- `:885` — `NextValue(latency_x2, phy.ios.rwds_i[0] | (latency_mode ==
  "fixed"))` — a genuine mode switch, where LUNA's `extra_latency | 1` is a
  constant

**`litex-hub/litehyperbus`, `litehyperbus/core/hyperram_ddrx2.py` (`76454e4`,
Gregory Davill)** — the only bounded read in the Migen family:
`:55` `timeout_counter = Signal(6)`, `:169-171` increment, `:178`
`If(~self.bus.cyc | (timeout_counter > 20)`.

## The trap: a flag is not an escape

`MJoergen/HyperRAM`, `src/hyperram/hyperram_ctrl.vhd` (`adeb1b6`), `READ_ST` at
`:204`:

```vhdl
if timeout_count > 0 then
   timeout_count <= timeout_count - 1;
else
   timeout_o <= '1';          -- :216-220 -- and nothing else
end if;
```

`timeout_o` is raised at `:219` and the FSM does not move. The only exit from
`READ_ST` is `read_return_count = 1` at `:225-229`, which needs device beats —
so on a silent device it raises the flag and hangs anyway. It looks like a
watchdog in review and is not one. Do not copy this shape.

## Checked in ours and found correct — do not re-audit

Established 2026-08-10 by reading the code and by simulation
(`tmp/logs/probe_fsm.log`, `tmp/logs/probe_gap.log`).

- **The `recovery_remaining` last-assignment-wins arrangement is sound.**
  Amaranth takes the last statement in a domain; the blanket load is emitted
  before the FSM's switch, so RECOVERY's own decrement wins while active and the
  load re-arms on exit. Measured: back-to-back memory writes give CS# high for
  **2 cycles / 20 ns at sync 100 MHz** and **3 cycles / 18 ns at 165 MHz**.
- **`T_CSHI_NS = 10.0` is right.** Datasheet Table 21 gives tCSHI 10 ns at
  100 MHz falling to 6 ns at 200 MHz. Ours is the 100 MHz figure at every clock,
  so it is conservative, never short.
- **tRWR is not an inter-transaction gap.** 35–40 ns, and Figures 27/28 measure
  it from CS# falling to first data — 14 CK of latency covers it several times
  over. It has been misread as a CS#-high requirement; it is not one.
- **CA[45] and CA[46] are constructed correctly.** `CA[45] = is_multipage =
  ~single_page`, `CA[46] = is_register`. Datasheet §9.1: *"CA[45] must be 1 as
  only linear single word register writes are supported."* True for every
  current caller — but see the issue, it is a caller-held invariant.
- **Register reads correctly take the full latency.** §9.2: *"read transaction
  with single or double initial latency … The latency count is defined in
  CR0[7:4]."* 14 CK for `CR0[3] = 1` is right; the read path is not at fault.
- **`rwds.e.eq(~is_register)` is right.** §9.2: *"The host must not drive RWDS
  during a write to register space."* `wtfuzz/hyperbus` and
  `blackmesalabs/hyperram` both get this wrong; we do not.
- **Nothing to pull from upstream LUNA.** `greatscottgadgets/luna` main is
  byte-identical to the vendored 0.2.3 — md5 `d234bd7b06cab9be07a33a09ea5f21d6`.
  `# TODO: implement recovery` is still at `psram.py:297` and `:662`;
  `extra_latency | 1` still at `:219` and `:605`. No commit has touched the file.

## Recommendation: fix ours, do not replace it

No replacement is available on Amaranth 0.5 + ECP5.

- **pulp, embelon, fpga-professional-association** — SystemVerilog. Best
  protocol coverage, wrong language.
- **OpenHBMC** — the most complete Verilog core: CR0 *and* CR1 init, tCSM
  splitting, and a data-recovery module (`OpenHBMC/hdl/hbmc_dru.v`) that removes
  read calibration entirely — *"No need to make any kind of calibration with new
  DRU"* (`README.md:21`), *"Successfully passes memory test at 200MHz (i.e. <
  400MB/s) on a real hardware (Spartan-7 + W956D8MBYA5I)"* (`:38`), the 128 Mbit
  sibling of our part. Locked to Xilinx 7-series `ISERDESE2`/`IDELAYE2` with no
  ECP5 equivalent. 781 LUT / 975 FF / 1 RAMB36E1 (`:22`).
- **Lattice `hyperram_mc`** — the right *shape*, but Lattice-licensed and built
  on Nexus primitives.
- **ChipFlow** — the one #90 names, and the only other Amaranth core. Sound
  structure and bounded by construction, but it has **no register read path at
  all** (`_hyperram.py:63`, `HRAMConfig(csr.Register, access="w")`), reaches
  only CR0, has no tCSHI, states *"no setup/chip configuration (use default
  latency)"* (`:27`), asserts `init_latency in (6, 7)` (`:70`), and samples DQ
  on the sync clock with no DDR primitives. It cannot back
  `hyperram_identify.py` and cannot run at our clocks. Design reference, not a
  dependency — which is what #90 already concluded.
- **LiteX `soc/cores/hyperbus.py`** — correct register handling and an explicit
  CS#-high state, but Migen, and its `REG-READ`/`DAT-READ` are as unbounded as
  ours.

Estimated cost of fixing ours, across `hyperram_controller.py` and
`hyperram_dqs_controller.py`:

| change | size | issue |
|---|---|---|
| DQS register-write data packing, register writes via RECOVERY | 2 lines each | done, `e6479e1` |
| beat counter + watchdog in READ_DATA/WRITE_DATA, sticky `timed_out` | ~30 lines | **[#316](https://github.com/awtoau/cynthion-workspace/issues/316) — the blocker** |
| tCSM burst chopper on the same counter | ~10 lines | [#317](https://github.com/awtoau/cynthion-workspace/issues/317) |
| export `fsm.state` | 1 line each | [#318](https://github.com/awtoau/cynthion-workspace/issues/318) |
| CR0/CR1 init sequencer gating traffic | ~40 lines | [#319](https://github.com/awtoau/cynthion-workspace/issues/319) |
| force CA[45]=1 for register space | 1 line each | [#320](https://github.com/awtoau/cynthion-workspace/issues/320) |
| sample RWDS mid-CA, not before it | ~10 lines | [#321](https://github.com/awtoau/cynthion-workspace/issues/321) |

## Correction to #90

#90 records *"litex-hyperram is unlicensed and seven years stale"*. That is true
of `gregdavill/litex-hyperram` (`9b17913`, 2019-12-06) and of
`litex-hub/litehyperbus` (`76454e4`, 2022-07-06), but the live LiteX HyperRAM
core is neither. It is `litex/soc/cores/hyperbus.py` in `enjoy-digital/litex`
itself — BSD-2, last touched 2026-06-19, with a CR0 init in
`litex/soc/software/libbase/hyperram.c` and a test suite in
`test/test_hyperbus.py`. The LiteX row above is that file; #90's dismissal
judged the wrong repository.
