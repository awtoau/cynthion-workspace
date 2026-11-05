"""The bitstream cache key includes the toolchain: versions and synthesis options (#603)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

BASE = ("Yosys 0.65+57", "nextpnr-0.10-74", "-run :check", "hierarchy -check", "--parallel-refine")

# Imported in a subprocess: soc_run does work at import. The identity is replaced by a literal
# so no tool is run.
PROGRAM = """
import soc_run
identities = {identities!r}
digests = []
for identity in identities:
    soc_run.toolchain_identity = lambda identity=identity: identity
    digests.append(soc_run.gateware_digest())
print(' '.join(digests))
"""


def digests(*identities):
    done = subprocess.run([sys.executable, "-c", PROGRAM.format(identities=list(identities))],
                          cwd=SCRIPTS, capture_output=True, text=True, check=True, timeout=120)
    return done.stdout.split()[-len(identities):]


def with_change(index, value):
    changed = list(BASE)
    changed[index] = value
    return tuple(changed)


def test_the_same_toolchain_gives_the_same_digest():
    first, second = digests(BASE, BASE)
    assert first == second


def test_a_different_yosys_changes_it():
    first, second = digests(BASE, with_change(0, "Yosys 0.70+329"))
    assert first != second


def test_a_different_nextpnr_changes_it():
    first, second = digests(BASE, with_change(1, "nextpnr-0.11.1-86"))
    assert first != second


def test_different_synth_options_change_it():
    first, second = digests(BASE, with_change(2, "-nowidelut -run :check"))
    assert first != second


def test_a_different_post_synth_script_changes_it():
    first, second = digests(BASE, with_change(3, "hierarchy -check\ntechmap -max_iter 1 -map x.v"))
    assert first != second


def test_different_placer_options_change_it():
    first, second = digests(BASE, with_change(4, "--no-such-flag"))
    assert first != second


# --- the real probe, against fake tools ---------------------------------------------
# nextpnr-ecp5 prints its version on STDERR; a probe that reads only stdout sees none, and
# then two different nextpnr versions hash alike.

def fake_tool(path, name, line, stream="", extra=""):
    path.mkdir(parents=True, exist_ok=True)
    tool = path / name
    tool.write_text(f"#!/bin/sh\necho '{line}' {stream}\n{extra}\n")
    tool.chmod(0o755)


def identity(tmp_path, nextpnr_override=None):
    suite = tmp_path / "suite"
    fake_tool(suite / "bin", "yosys", "Yosys FAKE 1.0")
    fake_tool(suite / "bin", "nextpnr-ecp5", "nextpnr FAKE 2.0", ">&2")
    (suite / "environment").write_text(f'export PATH="{suite / "bin"}:$PATH"\n')
    # `stable`: these tests are about the probe; a profile would add its own nextpnr path.
    env = {"PATH": "/usr/bin:/bin", "CYNTHION_OSS_CAD_SUITE": str(suite),
           "CYNTHION_TOOLCHAIN": "stable"}
    if nextpnr_override:
        override = tmp_path / "override"
        fake_tool(override, "nextpnr-ecp5", nextpnr_override, ">&2")
        env["CYNTHION_NEXTPNR_BIN"] = str(override)
    done = subprocess.run(
        [sys.executable, "-c", "import soc_run; print(soc_run.toolchain_identity()[:2])"],
        cwd=SCRIPTS, env=env, capture_output=True, text=True, check=True, timeout=120)
    return done.stdout.strip().splitlines()[-1]


def test_the_probe_reads_both_versions_including_nextpnr_on_stderr(tmp_path):
    seen = identity(tmp_path)
    assert "Yosys FAKE 1.0" in seen and "nextpnr FAKE 2.0" in seen


def script_identity(tmp_path, techmap_text):
    """The post-synth part of the identity when the script names a techmap file."""
    suite = tmp_path / "suite"
    fake_tool(suite / "bin", "yosys", "Yosys FAKE 1.0")
    fake_tool(suite / "bin", "nextpnr-ecp5", "nextpnr FAKE 2.0", ">&2")
    (suite / "environment").write_text(f'export PATH="{suite / "bin"}:$PATH"\n')
    techmap = tmp_path / "fix.v"
    techmap.write_text(techmap_text)
    env = {"PATH": "/usr/bin:/bin", "CYNTHION_OSS_CAD_SUITE": str(suite),
           "CYNTHION_TOOLCHAIN": "stable",
           "AMARANTH_script_after_synth": f"techmap -max_iter 1 -map {techmap}"}
    done = subprocess.run(
        [sys.executable, "-c", "import soc_run; print(repr(soc_run.toolchain_identity()))"],
        cwd=SCRIPTS, env=env, capture_output=True, text=True, check=True, timeout=120)
    return done.stdout.strip().splitlines()[-1]


def test_editing_a_techmap_the_script_names_changes_the_identity(tmp_path):
    before = script_identity(tmp_path, "module a; endmodule\n")
    same = script_identity(tmp_path, "module a; endmodule\n")
    after = script_identity(tmp_path, "module b; endmodule\n")
    assert before == same
    assert before != after


def test_a_nextpnr_override_is_the_one_identified(tmp_path):
    seen = identity(tmp_path, nextpnr_override="nextpnr OLD 0.1")
    assert "nextpnr OLD 0.1" in seen and "nextpnr FAKE 2.0" not in seen


def test_a_patched_binary_with_the_same_version_changes_the_identity(tmp_path):
    """Same version string, different yosys content: the digest must differ."""
    def identity_with(body):
        suite = tmp_path / "suite"
        fake_tool(suite / "bin", "yosys", "Yosys FAKE 1.0", extra=body)
        fake_tool(suite / "bin", "nextpnr-ecp5", "nextpnr FAKE 2.0", ">&2")
        (suite / "environment").write_text(f'export PATH="{suite / "bin"}:$PATH"\n')
        env = {"PATH": "/usr/bin:/bin", "CYNTHION_OSS_CAD_SUITE": str(suite),
               "CYNTHION_TOOLCHAIN": "stable"}
        done = subprocess.run(
            [sys.executable, "-c", "import soc_run; print(repr(soc_run.toolchain_identity()))"],
            cwd=SCRIPTS, env=env, capture_output=True, text=True, check=True, timeout=120)
        return done.stdout.strip().splitlines()[-1]

    assert identity_with("# a") == identity_with("# a")
    assert identity_with("# a") != identity_with("# b")
