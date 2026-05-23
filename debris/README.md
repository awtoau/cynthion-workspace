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

**Reason**: Content consolidated into WIKI.md (the single source of truth for documentation)

## Files Currently in This Directory

(Files will be added here during cleanup)

## Future Documentation

- Use WIKI.md for all project documentation
- Use design docs for forward-looking architecture (IMPLEMENTATION_PLAN.md, DESIGN_UART_WATCHDOG.md, etc)
- Use git commit messages for session summaries (no need for separate SESSION_SUMMARY.md)

## Archived Documents

### architecture.md
**Status**: Archived 2026-05-23 after consolidation to WIKI.md

**Why archived**: All hardware architecture, patch documentation, and isochronous support details have been consolidated into:
- [WIKI.md Hardware Architecture](../WIKI.md#hardware-architecture) — canonical reference for hardware design
- [GitHub Issues #8-11, #15, #43](https://github.com/awtoau/cynthion-workspace/issues) — implementation tracking

**What was in it**:
- Hardware block diagrams → now in WIKI.md
- Device states and transitions → now in WIKI.md  
- CONTROL_SWITCH architecture → now in WIKI.md
- Firmware patches (#8-#10, #43) → now in WIKI.md + GitHub issues
- Isochronous support (#11) → now in WIKI.md + Issue #11 detailed comment
- Development setup → referenced to WIKI.md and cyn CLI

**To restore**: If needed, git checkout commits 14db505 or earlier.

