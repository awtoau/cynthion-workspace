#!/usr/bin/env python3
"""Prepare local working trees for RV64 bring-up."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
R64 = ROOT / "riscv-64"
WORK = R64 / "work"
OUT = R64 / "out"

MIRROR = Path("/mnt/2tb/git_mirror/SpinalHDL/VexiiRiscv.git")
VEXII_WORKTREE = WORK / "vexiiriscv"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(cwd) if cwd else None)


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    if not MIRROR.exists():
        print(f"Mirror not found: {MIRROR}")
        print("Create the mirror first, then rerun this script.")
        return 1

    if not VEXII_WORKTREE.exists():
        run(["git", "clone", str(MIRROR), str(VEXII_WORKTREE)])
    else:
        run(["git", "-C", str(VEXII_WORKTREE), "remote", "set-url", "origin", str(MIRROR)])
        run(["git", "-C", str(VEXII_WORKTREE), "fetch", "--all", "--prune"])

    notes = OUT / "workdirs.txt"
    notes.write_text(
        "\n".join(
            [
                f"workspace={ROOT}",
                f"mirror={MIRROR}",
                f"vexii_worktree={VEXII_WORKTREE}",
            ]
        )
        + "\n",
        encoding="ascii",
    )
    print(f"Wrote {notes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
