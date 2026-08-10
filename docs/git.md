# Git & Submodules Reference

## Repositories

The workspace vendors seven Great Scott Gadgets repos as git submodules under
`repos/`, each pointing at an **awtoau fork** of the corresponding upstream.

| Submodule | Fork (origin)                     | Upstream                         |
|-----------|-----------------------------------|----------------------------------|
| apollo            | github.com/awtoau/awto-apollo            | greatscottgadgets/apollo            |
| cynthion          | github.com/awtoau/awto-cynthion          | greatscottgadgets/cynthion          |
| cynthion-hardware | github.com/awtoau/awto-cynthion-hardware | greatscottgadgets/cynthion-hardware |
| facedancer        | github.com/awtoau/awto-facedancer        | greatscottgadgets/facedancer        |
| luna              | github.com/awtoau/awto-luna              | greatscottgadgets/luna              |
| packetry          | github.com/awtoau/awto-packetry          | greatscottgadgets/packetry          |
| saturn-v          | github.com/awtoau/awto-saturn-v          | greatscottgadgets/saturn-v          |

Each submodule clone carries two remotes: `origin` (the awtoau fork) and
`upstream` (greatscottgadgets, read-only source of new commits).

**Local mirror:** `${GIT_MIRROR:-$HOME/git_mirror}/greatscottgadgets` — plain clones of the
seven upstream repos, usable as an offline reference.

## The four places a submodule lives

Any single submodule (e.g. facedancer) exists in four distinct locations:

1. **Local working checkout** — `repos/facedancer/` on disk (its real git data
   is in `.git/modules/repos/facedancer`). This is what you build against.
2. **Your fork on GitHub** — `awtoau/awto-facedancer` (the `origin` remote).
3. **The superproject pointer** — the workspace records a gitlink
   (`160000 <sha> repos/facedancer`) pinning an exact commit. Committing/pushing
   that goes to `awtoau/cynthion-workspace`.
4. **Upstream** — `greatscottgadgets/facedancer`, read-only, source of new commits.

Updating one place does not update the others. A typical sync touches all four:
fast-forward the fork (2) to upstream (4), fast-forward the local checkout (1)
to the fork, then commit the moved gitlink (3).

## Common operations

```bash
# Populate all submodules at their pinned commits
git submodule update --init

# See each submodule's pinned commit and describe
git submodule status

# Sync a fork to upstream on GitHub (server-side fast-forward, no local push)
gh api -X POST repos/awtoau/awto-<repo>/merge-upstream -f branch=main

# Fast-forward a local checkout to the fork after a fork sync
cd repos/<repo> && git fetch origin && git merge --ff-only origin/main

# Record the moved gitlink in the superproject
git add repos/<repo> && git commit

# Compare a fork tip to upstream (ahead/behind)
gh api repos/greatscottgadgets/<repo>/compare/<upstream-sha>...<fork-sha> \
  -q '.status, .ahead_by, .behind_by'
```

## Worktrees

`git worktree add` populates NO submodules, so a fresh worktree cannot build:
`repos/vexiiriscv` empty means the CPU cannot be regenerated at all.

```bash
./dev.py worktree-check     # is THIS checkout buildable? non-zero if not
./dev.py worktree-setup     # make it so
```

- Eleven submodules, not four — `repos/vexiiriscv/build.sbt` takes
  `ext/SpinalHDL` as a `ProjectRef`, so setup recurses.
- Each is checked out as a **linked git worktree of the superproject's own**
  `.git/modules/<name>`. Objects are shared; only files cost disk (127 MB here
  against 396 MB of shared history). Never copy a submodule tree into a
  worktree — that is what nearly put 225 MB into a commit (#365).
- Each submodule gets its own HEAD and index, so two worktrees can hold
  different pins. A shared `modules/` checkout cannot.
- No network: the pins are already in the shared object store.

`sources/**` is gitignored and so has no pin to check out.
`scripts/shared_paths.py:resolve_shared()` is the one rule for finding it —
env override, this checkout, then the main checkout behind it.

### `repos/apollo`'s pin is on no remote

Pin `90c8b7b6` exists in the superproject's object store and nowhere else;
`git fetch` of it answers `upload-pack: not our ref`. `git submodule update`
cannot repair a checkout that lacks it and a fresh clone cannot build. Worktrees
are unaffected because they share the store rather than fetching.
`worktree-check` reports it as a standing warning. Fix is to push it: #373.

## Notes

- `cynthion` is the fork that carries local work; it is intentionally **ahead**
  of upstream and is not fast-forwarded during routine syncs.
- The other forks are plain mirrors of upstream and can be fast-forwarded freely.
