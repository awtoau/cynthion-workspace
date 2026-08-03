# Handing work to Codex

Claude Code can delegate implementation to the OpenAI Codex CLI over MCP. Claude writes
the brief and reviews the diff; Codex does the typing.

## Setup, once

Codex CLI must be installed and logged in:

    codex --version        # 0.144.6 here
    codex login

Register its built-in MCP server with Claude Code:

    claude mcp add codex -s user -- codex mcp-server
    claude mcp list        # expect: codex - ✓ Connected

`-s user` puts it in `~/.claude.json`, so it is available in every project.

**Restart Claude Code afterwards.** MCP tools load at session start; a server registered
mid-session connects but its tools do not appear until the next one.

Two tools arrive: `codex` (run a session) and `codex-reply` (continue a thread by id).

## The one thing that does not work

**Codex cannot run git.** Its `workspace-write` sandbox leaves `.git/refs` read-only, so
`git switch -c` and `git commit` both fail.

That is not worth fighting. Tell Codex to leave its work uncommitted and commit it
yourself:

  * Claude owns branching, commits and pushes.
  * Codex owns the files.

This is better than the alternative anyway — commit messages here carry the reasoning,
and the reviewing agent is the one that has it.

`danger-full-access` would let it commit. Do not: the value of the sandbox is that a
misread instruction cannot rewrite history.

## Invoking it

    mcp__codex__codex(
        prompt=...,                    # the brief
        cwd="/path/to/repo",
        sandbox="workspace-write",
        approval-policy="never",       # or it blocks waiting for a human
    )

`approval-policy="never"` matters. The default asks before each shell command, and
nothing is there to answer.

## What the brief must carry

Codex does not read `CLAUDE.md` and does not know this project's rules. Every brief
repeats them, because a rule that is not in the prompt is not in force:

  * **No `sleep`, no `timeout`, in any shell command.** A hard error here.
  * Scripts in `./scripts/<name>.py`. No shell scripts.
  * Logs to `./tmp/logs/`. Temp under `./tmp/`, never system `/tmp`.
  * Comments explain why. Terse — lead sentence, bullets, table. No changelog prose.
  * Never the word "honest".
  * **Do not run git commands.** Leave changes in the working tree.
  * **Do not touch the board** when something else is using it.

Then the work itself. What has served best:

  * **Point at the issue** — `gh issue view N --comments`. The issues here are written as
    specifications, so this is most of the brief for free.
  * **State what has already been measured**, so it does not re-derive it. The 0.77 MB/s
    staging figure was what made #90's scope obvious.
  * **List the traps already paid for.** The three HyperRAM hold-don't-pulse faults each
    produced a plausible wrong answer rather than a failure; naming them turned them into
    assertions instead of a rediscovery.
  * **Name the discriminating test.** "Run the old arrangement alongside and assert it
    fails" produces a check that is known to discriminate rather than merely pass.

## Reviewing what comes back

Read the diff. Two things to look for specifically:

  * **Rules it dropped.** It is working from a prompt, not a rulebook.
  * **Verification it could not run.** Its sandbox blocks sockets, so anything needing
    sbt, a forkserver or a device fails for reasons unrelated to its work. It reports
    these; check they are environmental before treating them as failures.

## What it has done here

| issue | result |
|---|---|
| #144 | job queue and runner: `flock`, `/proc` holder detection, demo passing both the positive and the deliberately-wrong expectation |
| #90 | HyperRAM Wishbone window, 8 MiB at `0x20000000`, `main=1 exe=1`; sims 396 → 424 |

Both landed with the rules followed and the verification honestly scoped. Both needed a
human to commit.
