# Git and submodules

## Repositories

`.gitmodules` lists four submodules under `repos/`. `luna`, `facedancer`, `packetry`, `saturn-v` are not submodules.

| Submodule | Fork (origin) | Upstream |
|---|---|---|
| `repos/apollo` | `github.com/awtoau/awto-apollo` | `greatscottgadgets/apollo` |
| `repos/cynthion` | `github.com/awtoau/awto-cynthion` | `greatscottgadgets/cynthion` |
| `repos/cynthion-hardware` | `github.com/awtoau/awto-cynthion-hardware` | `greatscottgadgets/cynthion-hardware` |
| `repos/vexiiriscv` | none (`SpinalHDL/VexiiRiscv` directly) | `SpinalHDL/VexiiRiscv` |

- A fork clone carries two remotes: `origin` (awtoau fork) and `upstream` (greatscottgadgets, read-only).
- Local mirror: `${GIT_MIRROR:-$HOME/git_mirror}/greatscottgadgets`, plain clones of the upstream repos for offline reference.
- Pins and versions: [`toolchain-versions.md`](toolchain-versions.md#submodules).

## The four places a submodule lives

For example `repos/apollo`:

1. Local working checkout `repos/apollo/` (git data in `.git/modules/repos/apollo`); what you build against.
2. Your fork on GitHub (`origin`).
3. The superproject gitlink (`160000 <sha> repos/apollo`) pinning an exact commit; committing it goes to `awtoau/cynthion-workspace`.
4. Upstream, read-only source of new commits.

Updating one does not update the others. A sync touches all four: fast-forward the fork (2) to upstream (4), fast-forward the checkout (1) to the fork, commit the moved gitlink (3).

## Common operations

```bash
git submodule update --init                     # populate at pinned commits
git submodule status                            # pinned commit + describe

# sync a fork to upstream on GitHub (server-side fast-forward)
gh api -X POST repos/awtoau/awto-<repo>/merge-upstream -f branch=main

# fast-forward a local checkout to the fork, then record the gitlink
cd repos/<repo> && git fetch origin && git merge --ff-only origin/main
git add repos/<repo> && git commit

# ahead/behind of a fork tip against upstream
gh api repos/greatscottgadgets/<repo>/compare/<upstream-sha>...<fork-sha> \
  -q '.status, .ahead_by, .behind_by'
```

## Worktrees

`git worktree add` populates no submodules, so a fresh worktree cannot build; with `repos/vexiiriscv` empty the CPU cannot be regenerated.

```bash
./dev.py worktree-check     # is THIS checkout buildable? non-zero if not
./dev.py worktree-setup     # make it so
```

- Setup recurses: `repos/vexiiriscv/build.sbt` takes `ext/SpinalHDL` as a `ProjectRef`.
- Each submodule is checked out as a linked git worktree of the superproject's own `.git/modules/<name>`: objects shared, only files cost disk. Never copy a submodule tree into a worktree (nearly put 225 MB into a commit, [#365](https://github.com/awtoau/cynthion-workspace/issues/365)).
- Each submodule gets its own HEAD and index, so two worktrees can hold different pins (a shared `modules/` checkout cannot).
- No network: the pins are already in the shared object store.
- `sources/**` is gitignored and has no pin; `scripts/shared_paths.py:resolve_shared()` finds it: env override, this checkout, then the main checkout behind it.

## Notes

- `repos/cynthion` and `repos/apollo` carry local work and are intentionally ahead of upstream: not fast-forwarded in routine syncs (merge or rebase deliberately).
- `repos/cynthion-hardware` can be fast-forwarded freely (assumed a plain mirror: UNVERIFIED).
