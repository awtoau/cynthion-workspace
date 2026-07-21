# Debris Directory

Old/archived documentation files moved here during consolidation.

## Files That Were Deleted (Untracked, Not in Git)

These were never committed and have been removed:
- PHASE_1_RESULTS.md (265 lines) - Phase 1 build results
- PARALLELIZATION.md (339 lines) - Python 3.14 no-GIL parallelization details
- SESSION_SUMMARY.md (257 lines) - Session accomplishments summary
- QUICK_START_IMPROVED.md (295 lines) - Quick start guide
- PHASE_0_FINDINGS.md - Phase 0 toolchain review findings
- TOOLCHAIN_REVIEW.md - Detailed toolchain review
- TOOLCHAIN_VERSIONS.md - Tool versions comparison

**Reason**: Content consolidated into wiki.md (the single source of truth for documentation)

## Files Currently in This Directory

- `architecture.md` — archived hardware architecture and patch summary retained for historical reference

## Curated Prototype Archives

- `code/cynthion-workspace-prototype/` — preserved human-authored subset of an older workspace snapshot after removing generated files, VCS metadata, and exact duplicates
- `code/legacy_cli/` — superseded root CLI preserved after migration to the `cyn` command stack
- `code/cynthion-app-prototype/` — preserved human-authored subset of an older Flutter app snapshot after removing generated files and platform boilerplate
- `code/awto-cynthion-reference/` — remaining non-duplicate local deltas from an archived `awto-cynthion` snapshot after removing files identical to the live `/mnt/2tb/git/awtoau/awto-cynthion` checkout

## Future Documentation

- Use wiki.md for all project documentation
- Use docs/implementation_plans/ and docs/design_proposals/ for forward-looking architecture work.

Only archived or superseded documents should remain in `debris/`.
- Use git commit messages for session summaries (no need for separate SESSION_SUMMARY.md)

## Archived Documents

### architecture.md
**Status**: Archived 2026-05-23 after consolidation to wiki.md

**Why archived**: All hardware architecture, patch documentation, and isochronous support details have been consolidated into:
- [hardware_architecture.md](../docs/hardware_architecture.md) — canonical reference for hardware design
- [GitHub Issues #8-11, #15, #43](https://github.com/awtoau/cynthion-workspace/issues) — implementation tracking

**What was in it**:
- Hardware block diagrams → now in wiki.md
- Device states and transitions → now in wiki.md  
- CONTROL_SWITCH architecture → now in wiki.md
- Firmware patches (#8-#10, #43) → now in wiki.md + GitHub issues
- Isochronous support (#11) → now in wiki.md + Issue #11 detailed comment
- Development setup → referenced to wiki.md and cyn CLI

**To restore**: If needed, git checkout commits 14db505 or earlier.

