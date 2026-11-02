"""AMARANTH_nextpnr_opts set by the caller must reach the build (#429)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

IMPORT_THEN_PRINT = ("import fast_build_env as f, os;"
                     "print(os.environ['AMARANTH_nextpnr_opts'], '|', f.nextpnr_opts())")


def _run(code, env_opts):
    env = {k: v for k, v in os.environ.items() if k != "AMARANTH_nextpnr_opts"}
    if env_opts is not None:
        env["AMARANTH_nextpnr_opts"] = env_opts
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=SCRIPTS,
                         capture_output=True, text=True, check=True, timeout=30)
    return out.stdout.strip()


def test_caller_override_survives_import():
    assert _run(IMPORT_THEN_PRINT, "--seed 7") == "--seed 7 | --seed 7"


def test_default_is_router1_when_unset():
    out = _run(IMPORT_THEN_PRINT, None)
    assert out.split(" | ")[1] == "--parallel-refine --threads 31 --router router1"


def test_soc_run_pnr_opts_follow_override():
    out = _run("import soc_run; print(soc_run.PNR_OPTS)", "--seed 7")
    assert out.splitlines()[-1] == "--seed 7"
