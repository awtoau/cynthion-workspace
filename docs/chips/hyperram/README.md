# HyperRAM

The W956A8MBYA6I on Cynthion r1.4, and how it is being characterised.

**DQS is parked (Dan, 2026-11-02):** the shipping path is non-DQS, DQS board tests are paused, and the upstream-PR drafts are in [`../../deprecated/dqs/`](../../deprecated/dqs). Issues carry the label `hyperram-dqs-experiment`.

- **DQS code was removed at tag `dqs-last`** (the last commit that has it): `hyperram_dqs_controller.py`, `hyperram_dqs_phy.py`, the `CYNTHION_HYPERRAM_DQS` variant flag and `dqs` slug tag, `hr_fast`, the DQS sims and scripts, the `hr sel` / `hr sweep` shell verbs.
- Restore with `git checkout dqs-last -- <path>`, or revert the removal commits.
- Measurements and findings in these docs stay. Links to removed files are plain names; read one with `git show dqs-last:<path>`.
- Still in the engine, inert without DQS (#343): the capture-phase `sel` axis (`READCLKSEL` register, `SEL_VALUES`, `Census`, `pick_phase`).

**CR0/CR1 init:** the shipping path is firmware (`hyperram::init`, write + read-back each boot, #319). `gateware/soc/peripherals/hyperram_init.py` is a sim-verified gateware sequencer kept as a reference; it is wired into no build.

| document | what it is |
|---|---|
| [sequential-baseline.md](sequential-baseline.md) | **sequential throughput, non-RV path, from scratch** (#581) -- datasheet/public designs only; CK 60-165 figures, read eye, gap vs rated |
| [w956a8.md](w956a8.md) | **the part** — registers, timing, wiring, and every measured behaviour |
| [specifications.md](specifications.md) | **the numbers** — DC, current, refresh, and AC per speed grade, each with its source |
| [app-notes.md](app-notes.md) | **what the vendor says outside the datasheet** — the four Winbond application notes, the four questions they settle, and the three places they contradict the datasheet or each other |
| [bist-plan.md](bist-plan.md) | **the method** — what to measure, the matrix, and the rule that makes a result admissible |
| [survey.md](survey.md) | **everyone else** — nineteen open HyperBus controllers in RTL, the software and SoC drivers above them, and the simulation models |
| [byte-order.md](byte-order.md) | **which byte goes where** — the 32-bit DQS path's byte and word order, measured (code removed, `dqs-last`) |
| [models.md](models.md) | **the part without the board** — Winbond's own model, the open twin, and the testbench that holds them equal |
| [config-ac.md](config-ac.md) | **Winbond's own constants** — the vendor model's plaintext AC table, register defaults and CA words, transcribed off a file that cannot be committed |
| [sim-audit.md](sim-audit.md) | **every assertion in `soc_hyperram_sim.py` classified**, and `hyperram_sim_census.py` holds the file to it ([#346](https://github.com/awtoau/cynthion-workspace/issues/346)) |
| [2026-08-10-audit.md](2026-08-10-audit.md) | **our own faults** — six found, three fixed, and why no measurement survives |
| [pin-attributes.md](pin-attributes.md) | **the FPGA's own pads** — what DRIVE/SLEW/PULL/HYSTERESIS are set to, why they patch in 3 s, and why neither operating point can resolve them |

## Read the plan before trusting a number

Every HyperRAM figure recorded before 2026-08-06 was taken with at least one
broken instrument, and the two harnesses that exist disagree with each other
about BURSTDET. `bist-plan.md` dates each defect and states what has to be true
before a number counts.

The short version: a pass requires a negative control that **ran and failed**,
because zero errors and a comparator that never fired produce the same number.

## Where the code is

    gateware/soc/peripherals/hyperram*.py    the controller
    gateware/soc/peripherals/hyperram_init.py  CR0/CR1 init sequencer (#319), sim-only: scripts/soc/soc_hyperram_init_sim.py
    gateware/probes/hyperram/                the standalone harnesses
    scripts/hyperram_*.py                    the host-side drivers

Which of it has ever actually been run is [#189](https://github.com/awtoau/cynthion-workspace/issues/189), and the answer is not "all of
it".
