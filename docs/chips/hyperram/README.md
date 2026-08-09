# HyperRAM

The W956A8MBYA6I on Cynthion r1.4, and how it is being characterised.

| document | what it is |
|---|---|
| [w956a8.md](w956a8.md) | **the part** — registers, timing, wiring, and every measured behaviour |
| [bist-plan.md](bist-plan.md) | **the method** — what to measure, the matrix, and the rule that makes a result admissible |
| [controller-survey.md](controller-survey.md) | **everyone else** — nineteen open HyperBus controllers, what to borrow and what to avoid |
| [2026-08-10-audit.md](2026-08-10-audit.md) | **our own faults** — six found, three fixed, and why no measurement survives |

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
