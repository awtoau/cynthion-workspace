# HyperRAM

The W956A8MBYA6I on Cynthion r1.4, and how it is being characterised.

| document | what it is |
|---|---|
| [w956a8.md](w956a8.md) | **the part** — registers, timing, wiring, and every measured behaviour |
| [specifications.md](specifications.md) | **the numbers** — DC, current, refresh, and AC per speed grade, each with its source |
| [bist-plan.md](bist-plan.md) | **the method** — what to measure, the matrix, and the rule that makes a result admissible |
| [survey.md](survey.md) | **everyone else** — nineteen open HyperBus controllers in RTL, the software and SoC drivers above them, and the simulation models |
| [byte-order.md](byte-order.md) | **which byte goes where** — the 32-bit DQS path's byte and word order, measured, with the check that keeps it |
| [models.md](models.md) | **the part without the board** — Winbond's own model, the open twin, and the testbench that holds them equal |
| [config-ac.md](config-ac.md) | **Winbond's own constants** — the vendor model's plaintext AC table, register defaults and CA words, transcribed off a file that cannot be committed |
| [sim-audit.md](sim-audit.md) | **what the Python sim actually asserts** — all 143 checks classified, and what a retirement would cost |
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

    gateware/soc/peripherals/hyperram*.py    the controller and the DQS PHY
    gateware/probes/hyperram/                the standalone harnesses
    scripts/hyperram_*.py                    the host-side drivers

Which of it has ever actually been run is #189, and the answer is not "all of
it".
