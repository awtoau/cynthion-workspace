# Linux on Cynthion — the bring-up workspace

The analysis lives in [`../linux-on-cynthion/ANALYSIS.md`](../linux-on-cynthion/ANALYSIS.md);
this directory is the scripts, configs and captured results behind it.

Assets for the Cynthion RISC-V bring-up experiment on ECP5 LFE5U-12F.

**Scope changed.** This started as an RV64 Linux bring-up. 64-bit was evaluated
and parked — it does not fit the 12F usefully — and the live target is RV32.
The directory was renamed `riscv-64` → `riscv` to match. See
[`BRINGUP_PLAN.md`](BRINGUP_PLAN.md); RV64-specific assets that remain are
marked there.

**The sweep data this workspace produced was discarded.** Every Fmax and area
figure it generated is withdrawn — see [Withdrawn results](#withdrawn-results).
The sweep has not been re-run.

## Layout

| Path | Contents |
|---|---|
| `BRINGUP_PLAN.md` | Execution plan, phases, risks |
| `scripts/` | Legacy flow, steps 00–62 (see below) |
| `config/*.json` | Profile matrices. `profile_matrix_baremetal_x32.json` is the current one |
| `code/cynthion_rv64_min.dts` | RV64 device-tree skeleton — not revised for RV32 |
| `sim/tb_vexiiriscv_smoke.v` | Simulation testbench |
| `sim/vexii_ecp5_wrap.v` | Old PnR wrapper — prunes the design, superseded |
| `work/` | Gitignored scratch. See [`work/README.md`](work/README.md) |
| `out/` | Gitignored generated reports and logs |

VexiiRiscv is a submodule at `repos/vexiiriscv`, not a clone under `work/`.
Initialise with `git submodule update --init --recursive` — its `ext/`
submodules carry SpinalHDL.

## Current tooling

The sweep tooling was rebuilt at workspace root after the old pipeline was found
defective. Use these, not `62_generate_exhaustive_profile_matrix.py`:

| Script | Role |
|---|---|
| `scripts/riscv_matrix_config.py` | Generates the profile matrix. RV32, bare metal, no supervisor mode; sweeps cache sets 64/128/256 (4/8/16 KiB at one way) |
| `scripts/riscv_core_wrapper.py` | Synthesisable top: block RAM on both buses, one-cycle latency with a real ready handshake |
| `scripts/riscv_sweep_report.py` | Resolves results against the config JSON by profile name; XLEN and ISA base get their own columns |

Cache size is the axis that matters: the die has 56 DP16KD blocks (112 KiB)
shared between CPU, firmware, and USB buffers, so 16 KiB is swept to find the
block-RAM wall by measurement.

CoreMark has never been run. It needs firmware on the core, which needs CPU
bring-up. The report column is kept and left empty.

## Legacy scripts

`linux-on-cynthion/scripts/` is the original flow. It still runs, and its
simulation/place-and-route steps are still useful; its recorded numbers are not.

| Script | Role |
|---|---|
| `00_check_env.py` | Check host tools and mirror presence |
| `10_prepare_workdirs.py` | Prepare local working trees |
| `20_capture_soc_baseline.py` | Capture current SoC memory/peripheral constants |
| `30_qemu_linux_smoke.py` | QEMU RV64 Linux smoke boot (64-bit; parked) |
| `40_run_vexii_rtl_smoke.py` | Instruction-driven RTL smoke test |
| `41_run_vexii_postsynth_smoke.py` | Post-synthesis netlist smoke test |
| `42_run_vexii_nextpnr_timing.py` | ECP5-12F nextpnr timing/place flow |
| `43`, `44` | Metrics CSV append and trend report — **the CSV and report they wrote are deleted** |
| `45_scan_logs.py` | Scan logs for warning/error signatures |
| `60_run_profile_matrix.py` | Run one/all profiles from a config |
| `61_run_profile.py` | Per-profile engine |
| `62_generate_exhaustive_profile_matrix.py` | **Superseded** by `scripts/riscv_matrix_config.py` |
| `dev.py` | Runs 40 → 41 → 42 → 43 → 44 → 45 |

`dev.py` flags: `--skip-rtl-sim`, `--skip-postsynth-sim`, `--skip-timing`,
`--skip-log-scan`, `--fail-on-warnings`, `--threads <N>`.

`60_run_profile_matrix.py` usage:

```bash
python3 linux-on-cynthion/scripts/60_run_profile_matrix.py --list
python3 linux-on-cynthion/scripts/60_run_profile_matrix.py --profile soc_cumulative_uart --threads 8
python3 linux-on-cynthion/scripts/60_run_profile_matrix.py --profile core_i4k_d4k_bpred_dual --threads 8,16,32
python3 linux-on-cynthion/scripts/60_run_profile_matrix.py --all --threads 8
python3 linux-on-cynthion/scripts/60_run_profile_matrix.py --all --threads 8 --reset-history
```

## Output logs

- `riscv/out/sim/vexii_smoke_run.log`
- `riscv/out/sim/vexii_postsynth_run.log`
- `riscv/out/sim/vexii_ecp5_nextpnr.log`
- `riscv/out/sim/vexii_ecp5_timing_summary.txt`

## Withdrawn results

`metrics/` — the usage-history CSV and the generated ECP5 trend report —
was deleted, along with 1.5 GB of build outputs and 36 GB of per-job sbt
workspaces. The two tracked files are recoverable from git history at `2b84fe8~`.

Headline figures. The Fmax figures are struck; the area figures are more
defensible but include SoC glue, so they are not directly comparable with the
VexRiscv rows recorded elsewhere:

| Configuration | LUT4 | FF | Fmax |
|---|---|---|---|
| VexiiRiscv stripped | 6592 | 2695 | ~~73.4 MHz~~ |
| VexiiRiscv moondancer-like | 6876 | 3756 | ~~146.4 MHz~~ |

Four defects, found by reading the scripts rather than the outputs:

1. Fmax was routed at `--freq 25.0` with `--timing-allow-fail`, so the router
   stopped optimising once it cleared 25 MHz. It is a relaxed-target result, not
   a ceiling.
2. Bare-core builds used a wrapper that tied every core output to an
   unconnected wire and fed the instruction bus a constant nop, so synthesis
   deleted the output side — the generator log reports "567 signals were pruned".
   Those rows describe whatever survived, not a CPU.
3. Core builds carried no `output_prefix`, so features read out of filenames
   came back as "no features enabled" for 17 rows.
4. XLEN and ISA base were swept but never recorded, so 32- and 64-bit results
   were merged.

The one number with a stated measurement condition that survives: rerouting the
same I$+D$ configuration at a **200 MHz target gives 82.6 MHz**, against the
withdrawn 146.4 MHz.

A claim that the sweep had resolved the 73-vs-146 MHz question — that caches
doubled the clock — was retracted with the data. Both numbers came from the
same pipeline, and the two configurations also differ in XLEN. The question is
open and the proposed mechanism is untested.
