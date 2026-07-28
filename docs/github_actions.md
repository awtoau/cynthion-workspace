## GitHub Actions

### This workspace runs nothing on GitHub

There is no `.github/workflows/` in this repo, deliberately. The `fast` and
`full` workflows were deleted on 2026-07-28 and must not be reinstated.

Checks run locally instead — see [`scripts/check.py`](../scripts/check.py) and the
[README](../README.md#checks-run-locally-not-on-github):

```bash
./scripts/check.py --fast
```

Reasons this is local rather than hosted:

- The checks that matter need the real toolchain, the real free-threaded 3.15t
  interpreter, and (for anything beyond compilation) real hardware. A cloud
  runner can only approximate the first two and never the third.
- Hosted runs were actively misleading. Both flutter jobs carried
  `continue-on-error: true`, so they reported green for months while failing —
  6 analyzer warnings and a failing smoke test went unnoticed the whole time.
- Running locally is faster than queueing: the full fast set is ~3 s.

If something needs automating, extend `scripts/check.py`. Do not add a workflow.

### Upstream workflows in the submodules

The submodule repos are separate repositories — mostly forks tracking upstream —
and keep their own CI. Those are **not** covered by the rule above; deleting them
would diverge the forks from upstream and cause merge conflicts on the next sync.

| Repo | Workflow | What it does |
|------|----------|--------------|
| `awto-apollo` | `firmware.yml` | Apollo firmware for 6 board variants; push, PR, merge_group |
| `awto-cynthion` | `python.yml` | Python package, 3 OS × 5 Python versions (15 jobs); push, PR, weekly |
| `awto-saturn-v` | `build.yml` | Bootloader on 2 platforms; push, PR, weekly |
| `awto-luna` | `simulate.yml` | Gateware simulation |
| `awto-packetry` | 2 workflows | Build + AppImage packaging |

None of them build FPGA bitstreams, moondancer firmware, or the
analyzer/facedancer gateware — that work only ever happens locally, which is
another reason the local runner is the real check.
