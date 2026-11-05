"""CYNTHION_TOOLCHAIN picks the suite, nextpnr and synthesis options in one setting."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

PROGRAM = """
import os, soc_run
print(soc_run.TOOLCHAIN)
for key in ('CYNTHION_OSS_CAD_SUITE', 'CYNTHION_NEXTPNR_ECP5', 'AMARANTH_synth_opts',
            'AMARANTH_script_after_synth'):
    print(key + '=' + os.environ.get(key, '').replace('\\n', ' / '))
"""


def run(**env):
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("CYNTHION_", "AMARANTH_"))}
    done = subprocess.run([sys.executable, "-c", PROGRAM], cwd=SCRIPTS, env={**base, **env},
                          capture_output=True, text=True, timeout=120)
    return done


def settings(**env):
    done = run(**env)
    assert done.returncode == 0, done.stderr
    lines = done.stdout.strip().splitlines()
    return lines[-5], dict(line.split("=", 1) for line in lines[-4:])


def test_the_default_is_the_patched_yosys_with_its_own_suites_nextpnr_and_nowidelut():
    name, env = settings()
    assert name == "yosys070"
    assert env["CYNTHION_OSS_CAD_SUITE"].endswith("opt/fpga-main")
    assert env["CYNTHION_NEXTPNR_ECP5"] == ""       # the suite's own nextpnr (0.11.1)
    assert "-nowidelut" in env["AMARANTH_synth_opts"]
    assert "trellis_ff_without_m.v" not in env["AMARANTH_script_after_synth"]


def test_the_widelut_profile_keeps_the_default_synthesis_options():
    name, env = settings(CYNTHION_TOOLCHAIN="yosys070-widelut")
    assert name == "yosys070-widelut"
    assert "-nowidelut" not in env["AMARANTH_synth_opts"]
    assert env["CYNTHION_OSS_CAD_SUITE"].endswith("opt/fpga-main")


def test_the_retired_profiles_are_gone():
    for name in ("stable", "yosys070-stock", "yosys070-nextpnr010"):
        assert run(CYNTHION_TOOLCHAIN=name).returncode != 0


def test_an_explicit_variable_beats_the_profile():
    _, env = settings(AMARANTH_synth_opts="-run :check", CYNTHION_OSS_CAD_SUITE="/x/suite")
    assert env["AMARANTH_synth_opts"] == "-run :check"
    assert env["CYNTHION_OSS_CAD_SUITE"] == "/x/suite"


def test_an_unknown_profile_fails_and_names_the_choices():
    done = run(CYNTHION_TOOLCHAIN="no-such-profile")
    assert done.returncode != 0
    assert "no-such-profile" in done.stderr and "yosys070" in done.stderr
