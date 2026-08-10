#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause

"""Drain self-checking FPGA jobs while holding the board lock."""

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUEUE = ROOT / "fpga-jobs"
LOCK_PATH = ROOT / "tmp" / "fpga-job-runner.lock"
APOLLO_CLI = ROOT / "repos" / "apollo" / "apollo_fpga" / "commands" / "cli.py"

sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402


class JobError(RuntimeError):
    pass


class Output:
    """The shared log, plus a per-job copy while a job is running."""

    def __init__(self):
        self.job_handle = None

    def attach_job(self, path):
        self.job_handle = path.open("w", encoding="utf-8")

    def detach_job(self):
        if self.job_handle:
            self.job_handle.close()
            self.job_handle = None

    def say(self, line=""):
        emit(line)
        if self.job_handle:
            self.job_handle.write(line + "\n")
            self.job_handle.flush()

    def close(self):
        self.detach_job()


def inside(path, parent):
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def resolve_path(value, job_dir, label, must_exist=True):
    if not isinstance(value, str) or not value:
        raise JobError(f"{label} must be a non-empty path string")
    path = Path(value)
    if path.is_absolute():
        path = path.resolve()
    elif must_exist and not (job_dir / path).exists() and (ROOT / path).exists():
        path = (ROOT / path).resolve()
    else:
        path = (job_dir / path).resolve()
    if not inside(path, ROOT):
        raise JobError(f"{label} leaves the workspace: {value}")
    if must_exist and not path.exists():
        raise JobError(f"{label} does not exist: {path.relative_to(ROOT)}")
    return path


def command_argv(value, job_dir, label):
    if not isinstance(value, list) or not value or not all(
            isinstance(part, str) and part for part in value):
        raise JobError(f"{label} must be a non-empty array of strings")
    argv = list(value)
    if argv[0].endswith(".py"):
        script = resolve_path(argv[0], job_dir, f"{label}[0]")
        argv[0] = str(script)
        argv.insert(0, sys.executable)
    elif len(argv) > 1 and argv[1].endswith(".py"):
        argv[1] = str(resolve_path(argv[1], job_dir, f"{label}[1]"))
    return argv


def display_path(value):
    path = Path(value)
    if not path.is_absolute():
        return value
    if inside(path, ROOT):
        return str(path.relative_to(ROOT))
    return path.name


def load_job(job_dir):
    manifest_path = job_dir / "job.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise JobError(f"cannot read {manifest_path}: {error}") from error
    if not isinstance(manifest, dict):
        raise JobError("job.json must contain an object")

    required = {"schema", "hardware", "artifact", "driver", "expected",
                "negative_control", "restore"}
    missing = sorted(required - manifest.keys())
    if missing:
        raise JobError(f"job.json is missing: {', '.join(missing)}")
    if manifest["schema"] != 1:
        raise JobError(f"unsupported schema: {manifest['schema']!r}")
    if not isinstance(manifest["hardware"], bool):
        raise JobError("hardware must be true or false")
    if manifest["expected"] == manifest["negative_control"]:
        raise JobError("negative_control must differ from expected")

    artifact = manifest["artifact"]
    if not isinstance(artifact, dict):
        raise JobError("artifact must be an object")
    has_bitstream = "bitstream" in artifact
    has_build = "build" in artifact
    if has_bitstream == has_build:
        raise JobError("artifact needs exactly one of bitstream or build")

    if has_bitstream:
        bitstream = resolve_path(artifact["bitstream"], job_dir,
                                 "artifact.bitstream")
        build = None
    else:
        build_spec = artifact["build"]
        if not isinstance(build_spec, dict):
            raise JobError("artifact.build must be an object")
        for field in ("command", "flags", "sources", "output"):
            if field not in build_spec:
                raise JobError(f"artifact.build is missing: {field}")
        sources = build_spec["sources"]
        if not isinstance(sources, list) or not sources:
            raise JobError("artifact.build.sources must be a non-empty array")
        for index, source in enumerate(sources):
            resolve_path(source, job_dir, f"artifact.build.sources[{index}]")
        flags = build_spec["flags"]
        if not isinstance(flags, list) or not all(isinstance(x, str) for x in flags):
            raise JobError("artifact.build.flags must be an array of strings")
        build = command_argv(build_spec["command"], job_dir,
                             "artifact.build.command") + flags
        bitstream = resolve_path(build_spec["output"], job_dir,
                                 "artifact.build.output", must_exist=False)

    driver = manifest["driver"]
    if not isinstance(driver, dict) or "command" not in driver:
        raise JobError("driver.command is required")
    driver_argv = command_argv(driver["command"], job_dir, "driver.command")

    restore = manifest["restore"]
    if not isinstance(restore, dict) or "bitstream" not in restore:
        raise JobError("restore.bitstream is required")
    restore_bitstream = restore["bitstream"]
    if manifest["hardware"]:
        restore_bitstream = resolve_path(restore_bitstream, job_dir,
                                         "restore.bitstream")
    elif restore_bitstream is not None:
        raise JobError("a hardware-free job must set restore.bitstream to null")

    return manifest, build, bitstream, driver_argv, restore_bitstream


def run_command(argv, output, env=None):
    output.say(f"$ {' '.join(display_path(part) for part in argv)}")
    process = subprocess.Popen(
        argv, cwd=ROOT, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
    for line in process.stdout:
        output.say(line.rstrip("\n"))
    return process.wait()


def usb_identity(path):
    try:
        vid = int((path / "idVendor").read_text().strip(), 16)
        pid = int((path / "idProduct").read_text().strip(), 16)
    except (OSError, ValueError):
        return None
    names = []
    for field in ("manufacturer", "product"):
        try:
            names.append((path / field).read_text(errors="replace").strip().lower())
        except OSError:
            pass
    text = " ".join(names)
    known_id = ((vid, pid) in {(0x1d50, 0x615b), (0x1d50, 0x615c),
                              (0x1209, 0x0010)}
                or (vid == 0x1d50 and 0x6180 <= pid <= 0x61ff)
                or (vid == 0x1209 and 0x0001 <= pid <= 0x000f))
    known_name = any(word in text for word in ("cynthion", "apollo", "luna"))
    return (vid, pid) if known_id or known_name else None


def board_nodes(sys_root=Path("/sys"), dev_root=Path("/dev")):
    nodes = set()
    devices = sys_root / "bus" / "usb" / "devices"
    for device in devices.glob("*"):
        if usb_identity(device) is None:
            continue
        try:
            bus = int((device / "busnum").read_text())
            number = int((device / "devnum").read_text())
        except (OSError, ValueError):
            continue
        nodes.add(dev_root / "bus" / "usb" / f"{bus:03d}" / f"{number:03d}")

        for tty in (sys_root / "class" / "tty").glob("ttyACM*"):
            try:
                tty_device = (tty / "device").resolve()
                usb_device = device.resolve()
            except OSError:
                continue
            if tty_device == usb_device or usb_device in tty_device.parents:
                nodes.add(dev_root / tty.name)
    return nodes


def processes_holding(nodes, proc_root=Path("/proc")):
    wanted = {str(path.resolve()) for path in nodes if path.exists()}
    found = {}
    for process in proc_root.iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            handles = (process / "fd").iterdir()
            for handle in handles:
                if str(handle.resolve()) not in wanted:
                    continue
                command = (process / "cmdline").read_bytes()
                command = command.replace(b"\0", b" ").decode(errors="replace").strip()
                found[int(process.name)] = command or "?"
                break
        except OSError:
            continue
    return sorted(found.items())


def claim_runner(output, check_board):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = LOCK_PATH.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        lock.seek(0)
        owner = lock.read().strip() or "another FPGA job runner"
        lock.close()
        raise JobError(f"FPGA job runner lock is held by {owner}") from error
    lock.seek(0)
    lock.truncate()
    lock.write(f"pid {os.getpid()}: {' '.join(sys.argv)}\n")
    lock.flush()

    if check_board:
        holders = processes_holding(board_nodes())
        if holders:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
            detail = "; ".join(f"pid {pid}: {command}" for pid, command in holders)
            raise JobError(f"board is already held by {detail}")
    output.say(f"runner lock: {LOCK_PATH.relative_to(ROOT)} (pid {os.getpid()})")
    return lock


def expectation_env(base, job_dir, bitstream, control, expectation):
    env = base.copy()
    env.update({
        "FPGA_JOB_DIR": str(job_dir),
        "FPGA_JOB_BITSTREAM": str(bitstream),
        "FPGA_JOB_CONTROL": control,
        "FPGA_JOB_EXPECTED": json.dumps(expectation, separators=(",", ":")),
    })
    return env


def configure(bitstream, output):
    """Load a bitstream and confirm a design is running. Returns 0 on success.

    `expect="design"`: this is the restore path and the bitstream can be
    anything, so the part's DONE bit is the confirmation that generalises. A
    restore that leaves the FPGA blank is the next session's mystery (#360).
    """
    import soc_confirm

    output.say(f"$ configure_and_confirm {display_path(bitstream)}")
    return soc_confirm.configure_and_confirm(bitstream, expect="design")


def run_job(job_dir, completed, output):
    destination = completed / job_dir.name
    if destination.exists():
        output.say(f"FAILED: completed destination exists: {destination}")
        return False
    started = datetime.now(timezone.utc)
    result = {"schema": 1, "job": job_dir.name, "started": started.isoformat()}
    output.say()
    output.say(f"=== {job_dir.name} ===")
    output.attach_job(job_dir / "result.log")
    restore_bitstream = None
    hardware = False
    try:
        manifest, build, bitstream, driver, restore_bitstream = load_job(job_dir)
        hardware = manifest["hardware"]
        result["hardware"] = hardware
        result["expected"] = manifest["expected"]
        result["negative_control"] = manifest["negative_control"]
        output.say(f"expected: {json.dumps(manifest['expected'])}")
        output.say(f"negative control (must fail): "
                    f"{json.dumps(manifest['negative_control'])}")
        restored = display_path(str(restore_bitstream)) if restore_bitstream else None
        output.say(f"restore: {restored or 'not applicable'}")

        if build:
            output.say("--- build ---")
            build_env = os.environ.copy()
            build_env.update({
                "FPGA_JOB_DIR": str(job_dir),
                "FPGA_JOB_BITSTREAM": str(bitstream),
            })
            build_rc = run_command(build, output, build_env)
            result["build_returncode"] = build_rc
            if build_rc != 0:
                raise JobError(f"build exited {build_rc}")
            if not bitstream.exists():
                raise JobError(f"build did not produce {bitstream.relative_to(ROOT)}")

        controls = []
        for control, expectation in (("positive", manifest["expected"]),
                                     ("negative", manifest["negative_control"])):
            output.say(f"--- {control} control ---")
            env = expectation_env(os.environ, job_dir, bitstream,
                                  control, expectation)
            returncode = run_command(driver, output, env)
            controls.append({"control": control, "returncode": returncode})
        result["controls"] = controls

        positive_ok = controls[0]["returncode"] == 0
        negative_ok = controls[1]["returncode"] != 0
        if not positive_ok:
            raise JobError("positive expectation failed")
        if not negative_ok:
            raise JobError("negative control unexpectedly passed")
        result["status"] = "passed"
    except (JobError, OSError, KeyboardInterrupt) as error:
        result["status"] = "failed"
        result["error"] = str(error)
        output.say(f"FAILED: {error}")
    finally:
        if hardware and restore_bitstream:
            output.say("--- restore ---")
            restore_rc = configure(restore_bitstream, output)
            result["restore_returncode"] = restore_rc
            if restore_rc != 0:
                result["status"] = "failed"
                result["error"] = f"restore exited {restore_rc}"
                output.say(f"FAILED: restore exited {restore_rc}")

    result["finished"] = datetime.now(timezone.utc).isoformat()
    (job_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output.say(f"result: {result['status']}")
    output.detach_job()

    shutil.move(str(job_dir), str(destination))
    return result["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE,
                        help="queue root (default: fpga-jobs)")
    parser.add_argument("--hardware-free-only", action="store_true",
                        help="drain only hardware-free jobs without claiming the board")
    args = parser.parse_args()

    queue = args.queue.resolve()
    if not inside(queue, ROOT):
        parser.error("queue must be inside the workspace")
    queued = queue / "queued"
    completed = queue / "completed"
    queued.mkdir(parents=True, exist_ok=True)
    completed.mkdir(parents=True, exist_ok=True)
    output = Output()
    lock = None
    try:
        output.say(f"FPGA job runner: {datetime.now(timezone.utc).isoformat()}")
        lock = claim_runner(output, check_board=not args.hardware_free_only)

        jobs = sorted(path for path in queued.iterdir()
                      if path.is_dir() and not path.name.startswith("."))
        selected = []
        for job in jobs:
            if args.hardware_free_only:
                try:
                    manifest = json.loads((job / "job.json").read_text())
                except (OSError, json.JSONDecodeError) as error:
                    output.say(f"skipping unreadable job {job.name}: {error}")
                    continue
                if manifest.get("hardware") is not False:
                    output.say(f"skipping hardware job: {job.name}")
                    continue
            selected.append(job)

        if not selected:
            output.say("queue is empty")
            return 0
        passed = [run_job(job, completed, output) for job in selected]
        return 0 if all(passed) else 1
    except JobError as error:
        output.say(f"REFUSED: {error}")
        return 2
    finally:
        if lock:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        output.close()


if __name__ == "__main__":
    raise SystemExit(main())
