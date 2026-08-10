# HyperRAM: everyone else, 2026-08-10

Everything outside this repo that drives this part or one like it — nineteen
open-source HyperBus controllers in RTL, the software and SoC drivers above
them, and the simulation models. What each does, what is worth taking, and what
to avoid.

**Scope.** This is the *external* comparison. Our own faults are
[`2026-08-10-audit.md`](2026-08-10-audit.md); the part is
[`w956a8.md`](w956a8.md); the method is [`bist-plan.md`](bist-plan.md); the
vendor documents are [`../../../sources/README.md`](../../../sources/README.md).

**Surveyed 2026-08-10.** RTL by reading cloned sources (`tmp/hyperram-survey/`,
untracked and regenerable — the citations below are what survives); software by
`gh search code` plus the vendor SDK repos. Every claim is from reading the
file, not from its README. Commit and date are recorded per RTL row, so a stale
row is visible rather than assumed.

# Part 1 — RTL controllers

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

### Correction to #90

#90 records *"litex-hyperram is unlicensed and seven years stale"*. That is true
of `gregdavill/litex-hyperram` (`9b17913`, 2019-12-06) and of
`litex-hub/litehyperbus` (`76454e4`, 2022-07-06), but the live LiteX HyperRAM
core is neither. It is `litex/soc/cores/hyperbus.py` in `enjoy-digital/litex`
itself — BSD-2, last touched 2026-06-19, with a CR0 init in
`litex/soc/software/libbase/hyperram.c` and a test suite in
`test/test_hyperbus.py`. The LiteX row above is that file; #90's dismissal
judged the wrong repository.

# Part 2 — software and SoC drivers

Who drives this part from software, and what their init sequences say.

## Code that names `W956A8MBYA`

Four upstreams, and only four.

| where | file | what it is |
|---|---|---|
| [zephyrproject-rtos/zephyr](https://github.com/zephyrproject-rtos/zephyr/blob/main/drivers/memc/memc_mcux_flexspi_w956a8mbya.c) | `drivers/memc/memc_mcux_flexspi_w956a8mbya.c` + `dts/bindings/mtd/nxp,imx-flexspi-w956a8mbya.yaml` | the only OS driver for this exact part. NXP FlexSPI, i.MX RT |
| [JayHeng/imxrt-tool-ram-initscript](https://github.com/JayHeng/imxrt-tool-ram-initscript/blob/master/imxrt1060_flexspi_ad_b1_hyperram_init_w956a8mbya.jlinkscript) | `imxrt1060_flexspi_{ad,sd}_b1_hyperram_init_w956a8mbya.jlinkscript` | J-Link register-poke init: PLL, FlexSPI LUT, MPU |
| [aesc-silicon/elements-nafarr](https://github.com/aesc-silicon/elements-nafarr/blob/main/hardware/scala/nafarr/memory/hyperbus/sim/W956A8MBYA.scala) | `hardware/scala/nafarr/memory/hyperbus/sim/W956A8MBYA.scala` | SpinalHDL behavioural model — part 3 below |
| [nxp-mcuxpresso/mcux-component](https://github.com/nxp-mcuxpresso/mcux-component) | `gen_hal/zephyr/dts/bindings/mtd/nxp,imx-flexspi-w956a8mbya.yaml` | the binding, vendored |

Everything else in a code search is a Zephyr fork.

### The Zephyr driver never configures the device

`memc_mcux_flexspi_w956a8mbya.c` is 190 lines and its whole init is: install four LUT
sequences, reset the controller, read ID0, log it.

- LUT opcodes confirm the command set — `0xA0` read, `0x20` write, `0xE0` register
  read, `0x60` register write, all `8PAD` DDR.
- CA is split `RADDR_DDR 0x18` + `CADDR_DDR 0x10` — 24 + 16 = 40 bits, with the top
  8 in the opcode byte.
- `DUMMY_RWDS_DDR 0x07` — **7 dummy cycles**, i.e. the POR default latency code.
- **No CR0 or CR1 write anywhere.** It runs the part on POR defaults (`0x8F2F`), and
  every timing knob — `cs-interval`, `cs-hold-time`, `data-valid-time`,
  `ahb-write-wait-interval` — is a devicetree property on the *controller*, not a
  device register.
- `get_vendor_id()` reads 4 bytes from register address 0 and keeps the low 16 — ID0.
  It is a liveness check; nothing branches on the value.

So the one OS driver for this part proves the default configuration works and says
nothing about tuning it. It also has no tCSM handling, which for a memory-mapped AHB
master means the controller's own burst length is what keeps transactions short.

### NXP runs this part at 163.86 MHz with default CR0

The J-Link script's comment at the PLL write is explicit — *"Changes from 24 MHz →
327.72 MHz, which results FlexSPI to 163.86 MHz"* — and no CR0 write follows. The
highest third-party clock documented against this exact part, on the POR 7-clock
latency, and consistent with the 166 MHz grading of the `6I` on our board.

## Byte addresses vs word addresses — `0x1000` means two different registers

The trap most likely to be hit when porting a constant out of an SoC driver.

| convention | ID0 | CR0 | CR1 | used by |
|---|---|---|---|---|
| **word** (CA[31:24] × 0x800) | `0x0000` | `0x0800` | `0x0801` | the datasheets' own address map; our probes |
| **byte** (word × 2) | `0x0000` | `0x1000` | `0x1002` | STM32 OCTOSPI drivers, GAP SDK |

Three sources agree independently:
[`stm32_is66wvh8m8.h`](https://github.com/STMicroelectronics/stm32-mw-extmem-mgr/blob/main/custom/memories/stm32_is66wvh8m8.h)
writes drive strength to `0x1000` calling it CFR0;
[`IS66WVHxxM8.h`](https://github.com/codead/STM32H7_OSIP_HyperRAM/blob/master/HyperRAM/IS66WVHxxM8/IS66WVHxxM8.h)
defines `DIR0 0x0000`, `DIR1 0x0002`, `CR0 0x1000`, `CR1 0x1002`; and GreenWaves'
[`hyperram.c`](https://github.com/GreenWaves-Technologies/gap_sdk/blob/master/rtos/pmsis/bsp/ram/hyperram/hyperram.c)
read-modify-writes CR0 at `0x1000` (`0x80001000` once the register-space flag is on).

**Why it bites here specifically.** Word `0x1000` on the Winbond part is the
Manufacturer Information Register — read-only. A constant lifted from an STM32 driver
into a word-addressed probe lands on the MIR, and the symptom is a register write that
is silently refused while other writes on the same path succeed. That is exactly the
symptom this repo already recorded.

Second cross-vendor trap in the same registers: **CR1[1:0] does not mean the same
thing per vendor.** ISSI encodes refresh-interval *multipliers* (`00` = 2×, `01` = 4×,
`10` = default, `11` = 1.5×); Winbond encodes absolute tCSM (`01` = 4 µs, `10` = 1 µs
and reserved on our part).

## SoC HyperBus controllers, and what each exposes

| SoC / OS | controller | driver | device config from software? |
|---|---|---|---|
| NXP i.MX RT | FlexSPI | Zephyr `memc_mcux_flexspi_*`, MCUXpresso SDK | no — LUT + controller timings only |
| ST STM32 L4+/L5/H7/U5 | OCTOSPI HyperBus mode | `HAL_OSPI_HyperbusCfg`, `stm32-mw-extmem-mgr` | **yes** — `RwRecoveryTimeCycle`, `AccessTimeCycle`, `LatencyMode`, plus arbitrary register writes |
| ADI MAX32 | HPB | Zephyr [`memc_max32_hpb.c`](https://github.com/zephyrproject-rtos/zephyr/blob/main/drivers/memc/memc_max32_hpb.c) | **yes** — `config-regs` / `config-reg-vals` devicetree pairs, `latency-cycles`, `fixed-read-latency`, per-direction CS setup/hold/high |
| TI K3 (AM65x, J721e, J7200) | HBMC | Linux `drivers/mtd/hyperbus/hbmc-am654.c`, u-boot `drivers/mtd/hbmc-am654.c` | HyperFlash only |
| Renesas RZ/A, RZ/N | RPC-IF | Linux `drivers/mtd/hyperbus/rpc-if.c` | HyperFlash only |
| GreenWaves GAP8/GAP9 | uDMA HyperBus | [`rtos/pmsis/bsp/ram/hyperram/hyperram.c`](https://github.com/GreenWaves-Technologies/gap_sdk/blob/master/rtos/pmsis/bsp/ram/hyperram/hyperram.c) | **yes — the only one that retunes latency** |

**Upstream Linux and u-boot have no HyperRAM driver at all.** `drivers/mtd/hyperbus/`
is an MTD subsystem — `hyperbus-core.c` registers a CFI flash — so both TI and Renesas
entries are HyperFlash. There is no host-OS prior art to copy for the RAM case; the
prior art is all in RTOS memory-controller drivers and in RTL.

**ADI's MAX32 driver is the best shape to copy.** It is the only one that treats
"write these registers at init" as data rather than code: `config-regs` and
`config-reg-vals` are matched-length devicetree arrays with a `BUILD_ASSERT` on the
lengths, and latency/CS timings sit beside them. That is the interface
[#319](https://github.com/awtoau/cynthion-workspace/issues/319) wants for our CR0/CR1
sequencer.

### GreenWaves ships variable latency and a 3-clock count, deliberately

`hyperram.c:114-122` read-modify-writes CR0 and does two things this repo has only
discussed:

    reg &= ~(1 << 3);   // "Activate variable latency to avoid additionnal
                        //  latency when there is no refresh"
    reg &= ~0xf0;
    reg |=  0xe0;       // "Use 3 cycles of latency instead of the default 6 cycles"

- Clearing `CR0[3]` is **option 1** in [`w956a8.md`](w956a8.md), in production, with
  the reason stated as the reason we predicted (#335).
- `0xe0` = 3 clocks is **option 6**, which that page says *do not* — and the two do
  not conflict: 3 clocks is rated to 85 MHz and GAP8's uDMA HyperBus runs well below
  that. It is evidence for "the short codes are real", not for using them at our CK.

### ST's init raises the clock *after* configuring the device

`stm32_is66wvh8m8.h` is a 105-line data structure and the ordering in it is the
interesting part:

- `StartupConfig.Frequency = 50 MHz` → write CFR0 `0x7000` under mask `0x7000`
  (drive strength → 19 Ω) → `EXEC_OPT_CFG` → `OptionalConfig.Frequency = 100 MHz`.
- `LatencyMode = FIXED`, `AccessTimeCycle = 6`, `RwRecoveryTimeCycle = 6`,
  `WriteZeroLatency = ON`, `WrapSize = NOT_SUPPORTED`.

Configure slow, then raise the clock, is the vendor-blessed sequence — and it is what
a ceiling probe should do rather than configuring at the target clock. The drive
strength being the *one* register they bother to change is also a data point for
option 4 in [`w956a8.md`](w956a8.md).

# Part 3 — simulation models

| model | language | licence | fidelity |
|---|---|---|---|
| [nafarr `W956A8MBYA.scala`](https://github.com/aesc-silicon/elements-nafarr/blob/main/hardware/scala/nafarr/memory/hyperbus/sim/W956A8MBYA.scala) | SpinalHDL | CERN-OHL-W-2.0 | data path only — 8 MB array, CA decode, RWDS write mask. **No register space, no ID0/CR0, no tCSM, no refresh.** Latency is a hardcoded cycle count tuned to their own PHY (`writeLatency = 14`, and 104 in the non-DDR variant) |
| [`sergz72/FPGA` `hyperram_emulator.v`](https://github.com/sergz72/FPGA/blob/master/common/hyperram_emulator.v) | Verilog | — | open, synthesisable, usable with Icarus/Verilator |
| [GVSoC `hyperram`](https://github.com/gvsoc/gvsoc-core/blob/main/models/devices/hyperbus/hyperram.py) | Python wrapper over C++ | Apache-2.0 | models an S27KS0641; the behaviour is in `hyperram_impl`, not the Python |
| Winbond `W956X8MBY_verilog_p.zip` | SystemVerilog, encrypted | Winbond | **the golden model** — full register space, timing checks, DPD/hybrid-sleep/reset states. Runs under Diamond's Questa; see below |

## The vendor model runs — Diamond ships the key that decrypts it

`W956A8MBYA.modelsim.vp` carries one key block and only one:

    `pragma protect data_method  = "aes128-cbc"
    `pragma protect key_keyowner = "Mentor Graphics Corporation"
    `pragma protect key_keyname  = "MGC-VERIF-SIM-RSA-1"

Icarus, Verilator, cocotb, GHDL and Yosys can never read that, and neither can Aldec
Active-HDL — a different vendor key. The siblings `W956A8MBYA.vcs.vp` and `.nc.vp`
are the Synopsys and Cadence builds of the same model.

**Diamond 3.14 bundles Questa Sim Lattice OEM Edition, a Siemens build, which holds
the key.** Run it with `scripts/hyperram_vendor_model_sim.py`; the whole thing takes
under a second.

Two flags are needed and Winbond documents neither:

- **`-sv`.** The protected region is SystemVerilog. In Verilog-2001 mode vlog stops
  with *"syntax error in protected region"*, which reads like a decryption failure
  and is not one.
- **`+define+T166`** (or `T85`/`T100`/`T104`/`T133`/`T200`/`T250`). `Config-AC.v`'s
  AC-parameter block is an `ifdef` chain over the grades **with no default branch**,
  so with no grade defined it declares no timing parameters at all and every
  identifier in the protected region is undefined.

### What it reports, and why that matters

At power-up, unprompted:

    ==>ID_REG0     : (0x0c86)      ==>CONFIG_REG0 : (0x8f2f)
    ==>ID_REG1     : (0x0001)      ==>CONFIG_REG1 : (0xffc1)
    Manufacturer: Winbond (4'b0110)   col addr bits: 9   row addr bits: 13
    hyperbus X8 mode / Power supply 3V Device / Single Die mode -- 64Mb
    DIE0 address: 'h3F_FFFF ~ 'h00_0000

**Every one of those matches the board.** A register read driven over the bus returns
`0c 86` after exactly 14 CK, with the model narrating its own decode — *"The decode
command: Read Register ID0"*, *"Latency type: 1 (fixed), Latency code: 7, Latency
count: 14"*. So the values in [`w956a8.md`](w956a8.md) now have a second,
independent source that is not the board and not the datasheet prose.

`scripts/hyperram_vendor_model_sim.py` asserts all four registers plus the bus read
and exits non-zero on a mismatch, so it is a regression rather than a demo. The
testbench is `gateware/probes/hyperram/vendor_model_tb.sv`.

**This is the only model that implements the register space.** nafarr, sergz72 and
GVSoC all model the array and the CA and none answers a register read with plausible
contents, so anything that exercises `hyperram_identify.py` in simulation has to run
against this one.

**The plaintext half is worth having independently.** `Config-AC.v` ships unencrypted
in the same zip with the full AC parameter set per grade, including a **250 MHz**
block the datasheet has no column for, plus `tCSM` = 4000 ns below 85 °C / 1000 ns
above and a `` `define LA_85C `` to switch it.

# What is worth taking, from all three parts

1. **The three rules** at the top of part 1 — never let a device strobe be the only
   exit, one counter for tCSM and the read watchdog, export `fsm.state`.
2. **ADI's `config-regs` shape** for the CR0/CR1 init sequencer
   ([#319](https://github.com/awtoau/cynthion-workspace/issues/319)) — register writes
   as a data table with a length assertion, not open-coded states.
3. **ST's configure-then-accelerate ordering** for the ceiling probe: set CR0 at a
   clock the part is unambiguously rated for, *then* raise CK.
4. **Zephyr's LUT** as an independent check on our CA construction — the same
   `0xA0`/`0x20`/`0xE0`/`0x60` and the same 7-cycle default latency we drive.
5. **Nothing for the read path, from anyone.** No RTL core sweeps a capture phase and
   no software driver calibrates capture — they all sit behind a hard macro that owns
   it. Our `readclksel` sweep is ahead of all of it.
