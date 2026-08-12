# The board arbiter — one owner for the tty, the JTAG and what is configured

**Never open the console tty yourself. Never run `apollo configure` yourself.**
Submit a job.

```bash
./dev.py board run --variant bist1-ck120-dqs1-mirror0-mirrordiv4 "bist smoke"
./dev.py board status
```

- Server: [`scripts/board_arbiter.py`](../scripts/board_arbiter.py), HTTP on
  127.0.0.1:9100.
- Client: [`scripts/board.py`](../scripts/board.py), which **starts the server**
  if the port is down. No `daemon start` step.
- Records: `tmp/board-arbiter/jobs/<id>.json`, log `tmp/logs/board-arbiter.log`.
- Proof harness against real hardware:
  [`scripts/board_arbiter_proof.py`](../scripts/board_arbiter_proof.py).

## Why

Agents coordinated for the board in prose and reflashed each other's
measurements (#430): rows attributed to an unknown build, firmware 109 commits
stale while measurements were taken against it, `hyperram_verify` resolving one
variant's bitstream while the board ran another (#367).

The board is not just a tty. It is the tty **plus** Apollo JTAG **plus** the
state of what is configured. Nothing owned all three.

## The shape, and what is deliberately absent

- **One verb.** Submit a job, get a transcript with its provenance.
- **Verbs are operations, never leases.** There is no acquire/release; an agent
  never gets handed the tty.
- **One high-level verb, primitives underneath**, all on `POST /jobs` with a
  `kind`:

  | kind | does |
  |---|---|
  | `run` (default) | resolve bitstream → configure if what is loaded is not what was asked → **confirm** → run the commands → transcript |
  | `configure` | configure and confirm; no commands |
  | `confirm` | is a design running, and which failure if not |
  | `shell` | commands on whatever is loaded, provenance saying what that is |

- **Not building.** A variant with no bitstream is refused, naming what is
  built. `./dev.py` is the entry point;
  [`soc_build_fanout.py`](../scripts/soc_build_fanout.py) keeps the builds.
- **Not flashing firmware.** [`soc_run.py`](../scripts/soc_run.py) owns that.
- **Not command dispatch.** A job's commands are SoC *shell* commands. The only
  host program the arbiter runs is `apollo configure`, through
  [`soc_confirm.py`](../scripts/soc_confirm.py).

The scope rule is the whole design: it owns a resource, it does not dispatch
commands. The retired `cyn` (`debris/CYN_ARCHITECTURE.md`) died of the opposite.
**A second unrelated verb is the signal it is repeating `cyn`.**

## What a transcript carries

- the bitstream sha256, its path and size — the identity that makes #367
  impossible rather than remembered
- whether **this job** configured, or found the image already loaded
- the commit the **board** reports from `info` (firmware and gateware), not the
  host checkout — those differ whenever the board was flashed from elsewhere
- the `confirm` verdict by name
- the die temperature the part reports
- per command: the reply, `ok`/`timeout`/`preempted`, and the elapsed
- the host checkout's commit and dirty flag, and which worktree submitted it

## Why it cannot report a pass having run nothing

`passed` requires **all** of:

1. the pre-configure gate open (Apollo on the bus),
2. a `confirm` verdict of `ok` — `apollo configure` exiting 0 is not a running
   design (#360),
3. the board's own `info` reporting an image commit,
4. every command returning the shell's prompt,
5. at least one command, for `run` and `shell`.

Anything else is `failed`, `refused` or `preempted`, and the client exits
non-zero. [`tests/test_board_arbiter.py`](../tests/test_board_arbiter.py) drives
each leg to a failure.

## Going around it

- The tty's holders are read from `/proc`
  ([`board_holders.py`](../scripts/board_holders.py)). Anything but
  [`tio_user.py`](../scripts/tio_user.py) — which owns the tty by design and
  fans it out on 9000 — is a refusal naming the pid and command line.
- An interactive session stays possible: run `tio_user.py`, and the arbiter
  reads the console through its socket rather than competing for the node.

## The idle queue

`priority=idle` runs when nothing else is queued and is **preempted by a real
submission** — abandoned mid-sweep. Nothing depends on it finishing.

```bash
./dev.py board idle --variant bist1-ck120-dqs1-mirror0-mirrordiv4 \
                    --repeat 200 --budget 6 "bist all 8"
```

**Idle results are not measurements and cannot be made into one.** Three
barriers, because a caveat beside a number is read as the number:

1. separate directory — `tmp/board-arbiter/idle/`, never `results/hyperram/`
2. separate schema — `board-idle-observation`, which
   [`hyperram_matrix_diff.py`](../scripts/hyperram_matrix_diff.py) `load()`
   refuses, by schema **and** by path
3. none of a matrix run's keys — no `failures`, no `summary`, no `pins`

They earn their board time with **events**, never rows:

- a **wedge**, with the state that caused it still on the board until the next
  job configures
- a **tally that moved** between identical runs
- a **cell that changed verdict** — compared over cells *both* runs reached,
  since a preempted sweep prints a prefix
- **die drift** ≥ 5 °C (#341)

## Preemption, and what it costs

- The reader loop checks the preempt flag every 50 ms, so a sweep is abandoned
  mid-command rather than at the next command.
- An abandoned sweep leaves the board printing, so the arbiter marks it unknown:
  the next `run` configures (that is the reset), and a `shell` job is refused
  until something does. The cost of a preemption is one configure, ~2.5 s.

## Timing, measured on this board (2026-08-12, `bist1-ck120`, image `80a242f`)

| operation | measured |
|---|---|
| `info`, `bist status`, `bist smoke`, `bist latency 8` | 0.04–0.07 s |
| `bist all 1` / `bist all 8` (4096 cells) | 4.39 s / 4.59 s |
| configure + confirm | ~2.5 s |
| AUX console re-enumeration after a configure | 0.41 s (#419) |
| a job on an already-loaded variant, end to end | 0.27 s |

## One board, many worktrees

- The **port is the mutex**: agents work in git worktrees, and a lock under one
  worktree's `tmp/` arbitrates nothing.
- State and records live in the **main working tree**, resolved through
  `git rev-parse --git-common-dir`.
- The first client to find the port down starts the server **from its own
  checkout**; `status` reports which root that was.

## Superseded

`scripts/fpga_job_runner.py` and `fpga-jobs/` — a directory queue drained
one-shot, whose lock was worktree-local. Its `/proc` holder scan survives as
`board_holders.py`; its positive/negative-control discipline survives as the
arbiter's refusal rules and `board_arbiter_proof.py`.
