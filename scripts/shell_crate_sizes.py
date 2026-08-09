#!/usr/bin/env python3
"""What a `no_std` line-editor crate costs in `.text` on this CPU (#171).

Issue 171 asks whether the hand-rolled shell in `firmware/cynthion-soc/src/main.rs`
should be replaced by a crate. The deciding number is `.text`, because the shell
runs from memory-mapped flash through a 4 KiB direct-mapped I-cache and
`Cargo.toml`'s `opt-level = "z"` comment records 79% more code costing 5.4x the IPC.
A number nobody can re-take is an assertion, so this takes it.

## What it measures, and why it is a fair comparison

Every variant is the SAME program: the same 28 top-level command words, the same
per-command body (`dispatch`, `#[inline(never)]` so it cannot be inlined
differently under one editor than another), the same 16550, the same two shell
instances, the same `opt-level = "z"` + LTO + `codegen-units = 1` profile as the
shipping firmware, the same `riscv32imac-unknown-none-elf`, the same `memory.x`.

What differs is only the line editor and dispatcher in front of them. So

    .text(variant) - .text(base)

is the crate, and nothing else. `base` is the shell we already have, transcribed:
a 64-byte line buffer, backspace, echo, exact-match dispatch.

The harness is generated into `tmp/shellsize-run/` rather than kept as a tracked
crate, because it is a measurement instrument: it must be rebuilt from this file
so that a reader can see what was measured without trusting a directory.

Results and full build output go to `tmp/logs/shell_crate_sizes.log`.

    ./scripts/shell_crate_sizes.py
    ./scripts/shell_crate_sizes.py --keep     # leave tmp/shellsize-run/ in place
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "tmp" / "shellsize-run"
LOG = ROOT / "tmp" / "logs" / "shell_crate_sizes.log"
TARGET = "riscv32imac-unknown-none-elf"

# The variants, in the order they are reported. `None` is the baseline: no
# feature, so the `#[cfg(not(any(...)))]` module is the one that compiles.
VARIANTS = [
    ("base (today's shell)", None),
    ("+ prefix, TAB, history", "v_prefix"),
    ("ushell 0.4.0", "v_ushell"),
    ("embedded-cli 0.2.1", "v_embedded_cli"),
    ("menu 0.6.1", "v_menu"),
    ("nut-shell 0.1.2", "v_nutshell"),
    ("noline 0.5.1", "v_noline"),
    ("embedded-alloc 0.7 (heap only)", "v_alloc"),
    ("ratatui-core 0.1.2 (80x24)", "v_ratatui"),
]

CARGO_TOML = """\
[package]
name = "shellsize"
version = "0.1.0"
edition = "2021"

# The shipping firmware's dependencies, so the startup code and the panic path
# are the same shape in every variant.
[dependencies]
riscv = { version = "0.16", features = ["critical-section-single-hart"] }
riscv-rt = "0.18"

embedded-cli = { version = "0.2.1", optional = true }
embedded-io = { version = "0.6", optional = true }
menu = { version = "0.6.1", optional = true }
noline = { version = "0.5.1", optional = true }
nut-shell = { version = "0.1.2", optional = true }
ushell = { version = "0.4.0", optional = true }
nb = { version = "1", optional = true }
embedded-alloc = { version = "0.7", optional = true, default-features = false, \
features = ["llff"] }
ratatui-core = { version = "0.1.2", optional = true, default-features = false }

[features]
default = []
v_embedded_cli = ["dep:embedded-cli", "dep:embedded-io"]
v_noline = ["dep:noline", "dep:embedded-io"]
v_menu = ["dep:menu", "dep:embedded-io"]
v_ushell = ["dep:ushell", "dep:nb"]
v_nutshell = ["dep:nut-shell"]
v_prefix = []
v_alloc = ["dep:embedded-alloc"]
v_ratatui = ["dep:embedded-alloc", "dep:ratatui-core"]

# Identical to firmware/cynthion-soc/Cargo.toml. "z" is for SPEED here -- see the
# comment there, and #167.
[profile.release]
opt-level = "z"
lto = true
codegen-units = 1
debug = true
"""

CARGO_CONFIG = f"""\
[build]
target = "{TARGET}"

[{TARGET.join(['target."', '"'])}]
rustflags = ["-C", "link-arg=-Tmemory.x", "-C", "link-arg=-Tlink.x"]
"""


def emit(line: str = "", handle=None) -> None:
    print(line)
    if handle is not None:
        handle.write(line + "\n")
        handle.flush()


def section_sizes(elf: Path) -> dict[str, int]:
    """`.text`, `.rodata` and `.bss` from the ELF, via llvm-size."""
    out = subprocess.run(
        ["llvm-size", "-A", str(elf)], capture_output=True, text=True, check=True
    ).stdout
    sizes = {}
    for line in out.splitlines():
        match = re.match(r"^(\.\w[\w.]*)\s+(\d+)\s", line)
        if match:
            sizes[match.group(1)] = int(match.group(2))
    return sizes


def materialise(harness: Path) -> None:
    """Write the harness crate. Fails loudly rather than building something stale."""
    (RUN / "src").mkdir(parents=True, exist_ok=True)
    (RUN / ".cargo").mkdir(exist_ok=True)
    (RUN / "Cargo.toml").write_text(CARGO_TOML)
    (RUN / ".cargo" / "config.toml").write_text(CARGO_CONFIG)
    # The linker script is the firmware's own, not a copy that can drift.
    shutil.copy(ROOT / "firmware" / "cynthion-soc" / "memory.x", RUN / "memory.x")
    if not harness.exists():
        sys.exit(f"harness source missing: {harness}\n"
                 "It is generated by hand for #171 and lives beside this script; "
                 "see the module comment.")
    shutil.copy(harness, RUN / "src" / "main.rs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true",
                        help="leave tmp/shellsize-run/ in place afterwards")
    parser.add_argument("--harness", type=Path,
                        default=Path(__file__).resolve().parent / "shell_crate_sizes.rs",
                        help="the harness source (default: beside this script)")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    handle = LOG.open("w")
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    emit(f"shell_crate_sizes  {stamp}", handle)
    emit(f"target {TARGET}", handle)
    emit("", handle)

    materialise(args.harness)

    results = []
    for label, feature in VARIANTS:
        cmd = ["cargo", "build", "--release"]
        if feature:
            cmd += ["--features", feature]
        emit(f"--- {label}: {' '.join(cmd)}", handle)
        done = subprocess.run(cmd, cwd=RUN, capture_output=True, text=True)
        handle.write(done.stdout + done.stderr)
        if done.returncode != 0:
            emit(f"    BUILD FAILED -- see {LOG}", handle)
            results.append((label, None))
            continue
        elf = RUN / "target" / TARGET / "release" / "shellsize"
        sizes = section_sizes(elf)
        results.append((label, sizes))
        emit(f"    .text {sizes.get('.text', 0):6}  "
             f".rodata {sizes.get('.rodata', 0):6}  "
             f".bss {sizes.get('.bss', 0):6}", handle)

    base = next((s for label, s in results if s and label.startswith("base")), None)
    base_text = base.get(".text", 0) if base else 0

    emit("", handle)
    emit(f"{'variant':32} {'.text':>7} {'delta':>8} {'.rodata':>8} {'.bss':>7}", handle)
    emit("-" * 66, handle)
    for label, sizes in results:
        if sizes is None:
            emit(f"{label:32} {'FAILED':>7}", handle)
            continue
        text = sizes.get(".text", 0)
        emit(f"{label:32} {text:7} {text - base_text:+8} "
             f"{sizes.get('.rodata', 0):8} {sizes.get('.bss', 0):7}", handle)

    (ROOT / "tmp" / "shell_crate_sizes.json").write_text(
        json.dumps({"stamp": stamp, "target": TARGET,
                    "results": [{"variant": label, "sizes": sizes}
                                for label, sizes in results]}, indent=2))
    emit("", handle)
    emit(f"log  {LOG.relative_to(ROOT)}", handle)
    emit(f"json {(ROOT / 'tmp' / 'shell_crate_sizes.json').relative_to(ROOT)}", handle)

    if not args.keep:
        shutil.rmtree(RUN / "target", ignore_errors=True)
    handle.close()
    return 0 if all(s is not None for _, s in results) else 1


if __name__ == "__main__":
    sys.exit(main())
