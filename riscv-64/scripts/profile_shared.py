#!/usr/bin/env python3
"""Shared helpers for RV64 profiling scripts.

Keeps command execution, tool checks, and common dev/timing invocations
consistent across profile scripts.
"""

from __future__ import annotations

import contextlib
import fcntl
import pathlib
import shutil
import subprocess
import sys


def ensure_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"Missing required tool: {name}")
    return path


def run_logged(cmd: list[str], log_path: pathlib.Path, cwd: pathlib.Path | None = None) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n$ " + " ".join(cmd) + "\n")
        log.flush()
        proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, stdout=log, stderr=log, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}; see {log_path}")


def run_sbt_main(work_dir: pathlib.Path, sbt_arg: str, log_path: pathlib.Path) -> None:
    ensure_tool("sbt")
    run_logged(["sbt", sbt_arg], log_path, cwd=work_dir)


def shared_pipeline_lock_path(root: pathlib.Path) -> pathlib.Path:
    return root / "riscv-64" / "out" / "sim" / ".pipeline.lock"


@contextlib.contextmanager
def with_shared_pipeline_lock(root: pathlib.Path):
    lock_path = shared_pipeline_lock_path(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def run_dev_profile(
    root: pathlib.Path,
    tag: str,
    notes: str,
    target_mhz: float,
    threads: int,
    fail_on_warnings: bool,
    skip_log_scan: bool,
    log_path: pathlib.Path,
) -> None:
    cmd = [
        sys.executable,
        str(root / "riscv-64" / "scripts" / "dev.py"),
        "--skip-rtl-sim",
        "--skip-postsynth-sim",
        "--tag",
        tag,
        "--notes",
        notes,
        "--target-mhz",
        str(target_mhz),
    ]
    if threads > 0:
        cmd.extend(["--threads", str(threads)])
    if fail_on_warnings:
        cmd.append("--fail-on-warnings")
    if skip_log_scan:
        cmd.append("--skip-log-scan")
    # Core pipeline uses shared work/output paths; lock it to avoid artifact races.
    with with_shared_pipeline_lock(root):
        run_logged(cmd, log_path, cwd=root)


def build_nextpnr_cmd(
    nextpnr: str,
    json_path: pathlib.Path,
    textcfg_path: pathlib.Path,
    threads: int,
    freq_mhz: float = 25.0,
) -> list[str]:
    cmd = [
        nextpnr,
        "--12k",
        "--package",
        "CABGA256",
        "--speed",
        "8",
        "--json",
        str(json_path),
        "--textcfg",
        str(textcfg_path),
        "--timing-allow-fail",
        "--freq",
        str(freq_mhz),
    ]
    if threads > 0:
        cmd.extend(["--threads", str(threads)])
    return cmd
