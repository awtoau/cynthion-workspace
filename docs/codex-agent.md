# Handing work to Codex

- Setup, invocation, sandbox refusals and brief contents: `docs/codex/codex-agent.md` (canonical) in the private notes repo, rule preamble in `docs/codex/brief-template.md` there. Paths are relative to that repo's root, not this one.
- Claude owns branching and commits; Codex owns the files. Its sandbox mounts `.git` read-only: it cannot commit.

## Extra brief clauses for this project

- Do not touch the board when something else is using it.
- State up front which builds are too long to run, and that sbt, the forkserver and the device are unreachable from its sandbox (otherwise it spends the session on a blocked socket).

## What it has done here

| issue | result |
|---|---|
| [#144](https://github.com/awtoau/cynthion-workspace/issues/144) | job queue and runner: `flock`, `/proc` holder detection, demo passing both the positive and the deliberately-wrong expectation |
| [#90](https://github.com/awtoau/cynthion-workspace/issues/90) | HyperRAM Wishbone window, 8 MiB at `0x20000000`, `main=1 exe=1`; sims 396 -> 424 |

Both landed with the rules followed and verification scoped to what it could run. Both needed a human to commit.
