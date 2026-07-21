# cynthion-workspace Prototype Archive

This directory preserves the human-authored parts of an older `cynthion-workspace`
prototype after removing generated files, VCS metadata, platform boilerplate,
and exact duplicates from the archived subtree.

Preserved here:
- `cynthion_control.py` — older unified CLI tied to the `repos/` submodule layout
- `app/` source subset — Flutter prototype source, providers, screens, theme, and test
- `scripts/` — prototype build/check scripts and `cynthion-monitor.py`
- `docs/architecture.md` — prototype architecture note for the older workspace shape
- `.github/workflows/` — prototype CI workflow definitions
- `.gitmodules` — records the archived `repos/*` submodule layout

Not preserved from the old subtree:
- `build/`, `.dart_tool/`, `.git/`, `__pycache__/` — regenerable artifacts
- platform/generated boilerplate and exact duplicates of current repo files

Use this archive as historical reference, not current source of truth.