#!/usr/bin/env python3
#
# Build every declared cynthion-soc binary in its own feature set, and report
# .text. See awtoau/cynthion-workspace#362.
# SPDX-License-Identifier: BSD-3-Clause

"""Every `[[bin]]` in `firmware/cynthion-soc`, built in the features it declares.

    python3 scripts/soc_bin_matrix.py            # build them all, table of .text
    python3 scripts/soc_bin_matrix.py --only mono-rtic,workload-rtic

A binary with `required-features` is compiled by no other build: cargo skips
the target entirely when the feature is off, so `cargo check` on the crate says
nothing about it. Both RTIC binaries were broken on `main` for a day by two
edits to shared modules, and nothing was positioned to notice (#362).

**The target list is derived from `Cargo.toml`, not written here.** A new
`[[bin]]` is covered the moment it is declared -- the failure mode being fixed
is a target nobody builds, and a hand-maintained list here would reproduce it.

`.text` is reported for every binary because flash is the binding constraint
and LTO is load-bearing; a build that links but doubles in size is still news.

Output goes to the terminal and to `tmp/logs/soc_bin_matrix.log`.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from subprocess_timeout_from_history import run_bounded  # noqa: E402

CRATE = ROOT / "firmware" / "cynthion-soc"
MANIFEST = CRATE / "Cargo.toml"
LOG = ROOT / "tmp" / "logs" / "soc_bin_matrix.log"

# Its own build directory, so this never invalidates the artefact the bitstream
# packer reads: a build with different features re-unifies the whole dependency
# graph and would evict the shell from the shared cache on every run. Same
# reason `scripts/soc_feature_isolation_check.py` keeps one.
BUILD_DIR = ROOT / "tmp" / "bin-matrix-build"

# The shell, which has no `[[bin]]` because cargo takes it from `src/main.rs`.
DEFAULT_BIN = "cynthion-soc"

# Features a target needs ON TOP of its `required-features` to LINK, and why.
# `required-features` names what makes the target compile; these supply a
# `critical_section` implementation, which is a link-time symbol. The manifest
# says the same thing in prose at `rticwl`.
#
# Keys are checked against the declared targets below, so a renamed binary
# fails here rather than silently losing its extra feature.
LINK_FEATURES = {
    "workload-rtic": ["rticcs"],
}


def targets():
    """(name, features) for every buildable target, from the manifest."""
    manifest = tomllib.loads(MANIFEST.read_text())
    found = [(DEFAULT_BIN, [])]
    for entry in manifest.get("bin", []):
        found.append((entry["name"], list(entry.get("required-features", []))))

    declared = {name for name, _ in found}
    unknown = sorted(set(LINK_FEATURES) - declared)
    if unknown:
        raise SystemExit(f"LINK_FEATURES names targets that do not exist: {unknown}")

    return [(name, feats + LINK_FEATURES.get(name, [])) for name, feats in found]


def section_sizes(elf: Path) -> dict[str, int]:
    """Allocated section sizes, keyed by name."""
    tool = shutil.which("llvm-size") or shutil.which("size")
    if tool is None:
        raise SystemExit("neither llvm-size nor size is on PATH")
    out = subprocess.run([tool, "-A", elf.as_posix()],
                         capture_output=True, text=True, check=True).stdout
    sizes = {}
    for line in out.splitlines():
        match = re.match(r"^(\.\S+)\s+(\d+)\s+\d+", line)
        if match:
            sizes[match.group(1)] = int(match.group(2))
    return sizes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default="",
                        help="comma-separated binary names, instead of all")
    args = parser.parse_args()

    out: list[str] = []

    def emit(line: str = "") -> None:
        print(line)
        out.append(line)

    wanted = {n for n in args.only.split(",") if n}
    builds = [t for t in targets() if not wanted or t[0] in wanted]
    missing = sorted(wanted - {name for name, _ in builds})
    if missing:
        raise SystemExit(f"--only names no such target: {missing}")

    # The board's map, not `virt`'s: .cargo/config.toml already passes these,
    # and they are repeated because `RUSTFLAGS` in the environment replaces it.
    env = {**os.environ,
           "CARGO_TARGET_DIR": BUILD_DIR.as_posix(),
           "RUSTFLAGS": "-C link-arg=-Tmemory.x -C link-arg=-Tlink.x"}

    results: dict[str, dict[str, int]] = {}
    failed: list[str] = []

    for name, features in builds:
        argv = ["cargo", "build", "--release", "--bin", name]
        if features:
            argv += ["--features", ",".join(features)]
        emit(f"$ {' '.join(argv)}")
        # Bounded by what this binary's builds have taken before. A wedged
        # cargo is otherwise indistinguishable from a slow one (#295).
        proc = run_bounded(argv, cwd=CRATE, env=env, merge_stderr=True,
                           family=f"socbins:{name}")
        if proc is None:
            emit(f"  FAIL: {name} TIMED OUT")
            failed.append(name)
            continue
        if proc.returncode != 0:
            for line in (proc.stdout or "").splitlines():
                if line.startswith("error"):
                    emit(f"  {line}")
            emit(f"  FAIL: {name} did not build")
            failed.append(name)
            continue
        results[name] = section_sizes(
            BUILD_DIR / "riscv32imac-unknown-none-elf" / "release" / name)
        emit(f"  ok: {name}")

    emit()
    emit(f"{'binary':<20} {'features':<28} {'.text':>8} {'.rodata':>8} {'.bss':>8}")
    emit("-" * 76)
    for name, features in builds:
        sizes = results.get(name)
        feats = ",".join(features) or "(default)"
        if sizes is None:
            emit(f"{name:<20} {feats:<28} {'FAILED':>8}")
            continue
        emit(f"{name:<20} {feats:<28} {sizes.get('.text', 0):>8} "
             f"{sizes.get('.rodata', 0):>8} {sizes.get('.bss', 0):>8}")

    emit()
    if failed:
        emit(f"RESULT: FAIL - {', '.join(failed)}")
        rc = 1
    else:
        emit(f"RESULT: PASS - all {len(builds)} targets build")
        rc = 0

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    print(f"\n(log written to {LOG})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
