# Running upstream CI locally, before submitting anything

`scripts/upstream_ci.py` clones a GreatScottGadgets repo into `tmp/`, checks out
whatever you name, and runs the exact commands that repo's own workflow files
run. The clone is destroyed and re-made every run, so it cannot touch `repos/*`.

This exists because upstream CI is the gate a submitted patch has to pass, and
there is no reason to learn about a failure from a maintainer when the same
check runs here in seconds.

Related: `docs/upstream-patch-process.md` (the rules a patch must satisfy),
`docs/upstream-patch-plan.md` (the ordering), issue [#102](https://github.com/awtoau/cynthion-workspace/issues/102) (the parked plan).

## Usage

    ./scripts/upstream_ci.py luna                       # upstream main
    ./scripts/upstream_ci.py luna --pr 301              # someone else's PR head
    ./scripts/upstream_ci.py apollo --pick 0e9bfb1      # our commit on clean upstream
    ./scripts/upstream_ci.py apollo --job host-tests    # one job
    ./scripts/upstream_ci.py luna --list                # show jobs, run nothing

`--pick` cherry-picks from `repos/<repo>` onto clean upstream. That is the
operation a submission actually performs, so a conflict here is the conflict a
maintainer would hit — surfaced before anyone is asked to look at it.

Full history is cloned only when `--pick` or `--patch` is given; otherwise the
clone is shallow. `git apply -3` and `git cherry-pick` both need real blobs and
fail on a `--depth 1` clone with *"repository lacks the necessary blob"*.

## What upstream CI actually is

| repo | automated checks on a PR |
|---|---|
| `luna` | GH Actions `simulations`: `unittest discover -t . -s tests` on Python 3.9–3.13. Plus a Jenkins HIL job. |
| `apollo` | GH Actions `python` (`unittest discover -p jtag_svf.py`) and `Firmware` (6-board build matrix). |
| `cynthion` | GH Actions `Python`: unit tests + analyzer elaboration, on 3 OSes × 5 Pythons. |
| `facedancer` | **none** — no `.github/workflows` at all. |
| `luna-soc` | **none** — no `.github/workflows` at all. |

Two things follow, and both matter more than the table:

**The GitHub-side signal is weak.** LUNA's entire Actions gate is 93 unit tests
that run in 2.2 seconds. Anything a patch breaks that is not covered by those 93
tests will go green. Measured, not assumed: PR [#301](https://github.com/awtoau/cynthion-workspace/issues/301) fixes an inverted fanout
direction in `HandshakeExchangeInterface`, and the suite passes identically with
and without the fix (93 tests, OK, both). Upstream CI would not have caught the
bug it fixes.

**The signal that would matter is unavailable.** LUNA's Jenkins
hardware-in-the-loop job is the only check that exercises real hardware, and it
is `ERROR` on every open PR ([#301](https://github.com/awtoau/cynthion-workspace/issues/301), [#303](https://github.com/awtoau/cynthion-workspace/issues/303), [#304](https://github.com/awtoau/cynthion-workspace/issues/304)) with logs behind
`jenkins.greatscottgadgets.com`. We cannot run it and cannot read it.

So upstream CI is worth passing, but it is not worth *submitting for*. Our own
hardware tests remain the real gate — see `docs/upstream-patch-process.md`.

**Fork PRs from first-time contributors do not run Actions until a maintainer
approves them.** PR [#301](https://github.com/awtoau/cynthion-workspace/issues/301) has been open since 2026-03-13 with zero comments, zero
reviews, and no Actions run at all. PRs from known collaborators (miek,
martinling, mndza) run normally. A first submission sits in that queue.

## Local baseline, 2026-08-03

Clean upstream `main`, no patches, Fedora `arm-none-eabi-gcc` 15.2.0, free-threaded
CPython 3.15.0b3, amaranth 0.5.9.

| job | result |
|---|---|
| `luna` sim-tests | PASS — 93 tests, 2.2 s |
| `apollo` host-tests | PASS — 17 tests |
| `apollo` firmware-cynthion | PASS — text 13500, data 260, bss 3268 |
| `apollo` firmware-samd11_xplained | PASS — text 11984 |
| `apollo` firmware-qtpy | PASS — text 12408 |
| `apollo` firmware-cynthion-r0.2 | PASS — text 10056 |
| `apollo` firmware-cynthion-r0.4 | PASS — text 13500 |
| `apollo` firmware-raspberry_pi_pico | **FAIL — pre-existing, ours** |

The pico failure is a local toolchain mismatch, not an upstream defect and not
caused by any patch: Fedora's GCC 15.2.0 against the vendored pico-sdk gives
`declaration for parameter '__uint8_t' but no such parameter` and
`button.c:56: error: expected '{' at end of input`. Upstream's CI pins an older
compiler via `carlosperate/arm-none-eabi-gcc-action`. **Always run the control**
(same job, no patch) before attributing a failure to a patch — that is how this
one was identified.

Note `apollo` upstream main builds cynthion at 13760 B of 14336 B (96.0%),
consistent with the 96.04% recorded in issue [#102](https://github.com/awtoau/cynthion-workspace/issues/102). The flash budget is the
binding constraint on any firmware patch series.
