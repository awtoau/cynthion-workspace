# Handing work to Codex — here

How Codex is set up, invoked, what its sandbox refuses, and what a brief must carry
lives in a separate private notes repo, as `docs/codex/codex-agent.md` (canonical),
with the rule preamble to paste into every brief in `docs/codex/brief-template.md`.
Those paths are relative to that repo's root, not this one.

The short version: Claude owns branching and commits, Codex owns the files — its
sandbox mounts `.git` read-only, so it cannot commit and should not try.

## Extra brief clauses for this project

  * **Do not touch the board** when something else is using it.
  * Say up front which builds are too long to run, and that sbt, the forkserver and the
    device are unreachable from its sandbox — otherwise it spends the session on a
    blocked socket.

## What it has done here

| issue | result |
|---|---|
| #144 | job queue and runner: `flock`, `/proc` holder detection, demo passing both the positive and the deliberately-wrong expectation |
| #90 | HyperRAM Wishbone window, 8 MiB at `0x20000000`, `main=1 exe=1`; sims 396 → 424 |

Both landed with the rules followed and the verification scoped to what it could
actually run. Both needed a human to commit.
