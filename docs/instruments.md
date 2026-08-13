# Instruments: checks that cannot report the failure they exist to catch

The recurring defect on this project is not an unfixed bug. It is a **check that
passes for a reason unrelated to what it claims to measure** — and every one cost
more than the bug would have, because it also removed the suspicion.

**Index:** [`README.md`](README.md) · the board's own rules
[`board-arbiter.md`](board-arbiter.md)

## The four shapes

| shape | what it looks like | example |
|---|---|---|
| **verifies itself** | the check reads back what it just wrote, never what was stored | `hr ramp w` reported `256/256 correct` on a part holding `RESET#` asserted |
| **measures against a belief** | the model under test is one we wrote | every HyperRAM sim ran the open twin; the vendor model was installed and never asked ([#426](https://github.com/awtoau/cynthion-workspace/issues/426)) |
| **cannot go red where anyone looks** | correct, working, on no list | `hyperram_phy_rwds_sim.py` sat red on main; nothing ran it ([#401](https://github.com/awtoau/cynthion-workspace/issues/401)) |
| **reports a number that cannot move** | the quantity is real, the denominator is wrong | `rtic` prints a LIFETIME stall ratio: 121/566 idle, 120/566 after 215,040 cache-missing accesses ([#530](https://github.com/awtoau/cynthion-workspace/issues/530)) |

## The rules that follow

**A check must be shown to fail.** Corrupt the parity, delay the turnaround,
drop the acknowledge — and watch it go red. `scripts/swd_host_sim.py` and
`scripts/sbu_port_sim.py` run their negative controls on every invocation, which
is why their passes mean something.

**Do not build a control for the mechanism you replaced.** Proving the old way
was worse is cost without information; cite the issue. Prove the NEW check can
fail.

**A check that is not on the gate does not exist.** `check.py` was 12/12 green
on a tree whose default variant could not be built, with a dead doc link, and
with 499 unlinked issue references — three checkers existed and none ran
([#526](https://github.com/awtoau/cynthion-workspace/issues/526)).

**Read the tool's own log.** `ABC: Error: Abc_FrameUpdateGia(): Transformation
has failed.` sat at line 25471 of a report nobody opened, across every build,
because the only reader was a script run by hand ([#527](https://github.com/awtoau/cynthion-workspace/issues/527)).

**Exit status is not evidence.** `gh issue close` exits 0 on a close GitHub's
rate limiter dropped; `nextpnr` misses a constraint and the build still
succeeds; a `--build-only` run reports success having produced no bitstream.
Read the state back.

**Name what produced a measurement.** Every board result carries the bitstream
sha256, the commit **the board reports**, and the `confirm` verdict, because a
figure attributed to the wrong build is worse than no figure
([`board-arbiter.md`](board-arbiter.md), [#430](https://github.com/awtoau/cynthion-workspace/issues/430)).

## Why the cost is asymmetric

A missing check leaves a known gap. A **passing** check that cannot fail closes
the question — so the next reader inherits a wrong answer and no reason to look.
Every HyperRAM throughput figure in this project was deleted rather than
annotated for exactly this reason: five overlapping faults were live and no
number could be matched to which were still present
([`chips/hyperram/w956a8.md`](chips/hyperram/w956a8.md)).

**A superseded measurement is deleted, not annotated.** A number kept beside a
warning that it is wrong is longer than no number and still gets quoted.
