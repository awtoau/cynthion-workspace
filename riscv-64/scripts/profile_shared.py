#!/usr/bin/env python3
"""Shared helpers for RV64 profiling scripts.

Keeps command execution, tool checks, and common dev/timing invocations
consistent across profile scripts.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import os
import pathlib
import shutil
import subprocess
import sys
import time


def _ts() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def append_event(log_path: pathlib.Path | None, message: str) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"[{_ts()}] {message}\n")


def ensure_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"Missing required tool: {name}")
    return path


def run_logged(
    cmd: list[str],
    log_path: pathlib.Path,
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    wd = str(cwd) if cwd else os.getcwd()
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{_ts()}] START cwd={wd} cmd={' '.join(cmd)}\n")
        log.flush()
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=log,
            stderr=log,
            text=True,
        )
        duration = time.monotonic() - start
        log.write(f"[{_ts()}] END rc={proc.returncode} duration_sec={duration:.3f}\n")
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}; see {log_path}")


def run_sbt_main(
    work_dir: pathlib.Path,
    sbt_arg: str,
    log_path: pathlib.Path,
    *,
    sbt_boot_dir: pathlib.Path | None = None,
    sbt_dir: pathlib.Path | None = None,
    ivy_home: pathlib.Path | None = None,
    coursier_cache: pathlib.Path | None = None,
    no_server: bool = False,
) -> None:
    ensure_tool("sbt")
    cmd = ["sbt"]
    if no_server:
        cmd.extend(["--batch", "--no-server"])
    if sbt_boot_dir is not None:
        sbt_boot_dir.mkdir(parents=True, exist_ok=True)
        cmd.extend(["--sbt-boot", str(sbt_boot_dir)])
    if sbt_dir is not None:
        sbt_dir.mkdir(parents=True, exist_ok=True)
        cmd.extend(["--sbt-dir", str(sbt_dir)])
    if ivy_home is not None:
        ivy_home.mkdir(parents=True, exist_ok=True)
        cmd.extend(["--ivy", str(ivy_home)])
    cmd.append(sbt_arg)

    env = os.environ.copy()
    if coursier_cache is not None:
        coursier_cache.mkdir(parents=True, exist_ok=True)
        env["COURSIER_CACHE"] = str(coursier_cache)
    run_logged(cmd, log_path, cwd=work_dir, env=env)


def shared_pipeline_lock_path(root: pathlib.Path) -> pathlib.Path:
    return root / "riscv-64" / "out" / "sim" / ".pipeline.lock"


@contextlib.contextmanager
def with_shared_pipeline_lock(root: pathlib.Path, event_log: pathlib.Path | None = None, section: str = "pipeline"):
    lock_path = shared_pipeline_lock_path(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start_wait = time.monotonic()
    append_event(event_log, f"LOCK_WAIT section={section} path={lock_path}")
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        waited = time.monotonic() - start_wait
        append_event(event_log, f"LOCK_ACQUIRED section={section} waited_sec={waited:.3f}")
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            append_event(event_log, f"LOCK_RELEASED section={section}")


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
