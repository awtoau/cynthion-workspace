#!/usr/bin/env python3
#
# One build directory per variant, and one definition of what a variant is. #351.
# SPDX-License-Identifier: BSD-3-Clause

"""Does the build directory separate the variants that the cache separates?

Two builds could not run at once because `top.py` had one fixed `build_dir` for
every variant. Deriving it from `variant.py` -- the same table `soc_run.py`
hashes into the bitstream cache key -- is what makes a CK ladder N concurrent
builds instead of N sequential ones.

The failure this guards against is not a collision, which is loud. It is the
directory and the cache key disagreeing: a build that reuses another variant's
artifacts while the digest beside them says they are fresh.

Asserted rather than eyeballed:

  * variants that differ get different directories
  * a variant that does not differ gets the same directory every time, including
    unset versus set-to-the-default
  * every variable that changes the directory also changes the cache key, and
    every variable in the cache key also changes the directory
  * `top.py` reads its own variant values through the same table, so a sixth
    variable cannot be added to one and missed by the other
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

from soc import variant  # noqa: E402

TOP = ROOT / "gateware" / "soc" / "top.py"

# Two variants that differ in one axis each, plus the default.
DEFAULT: dict = {}
BIST = {"CYNTHION_HYPERRAM_BIST": "1"}
BIST_CK120 = {"CYNTHION_HYPERRAM_BIST": "1", "CYNTHION_HYPERRAM_CK_MHZ": "120"}
BIST_NODQS = {"CYNTHION_HYPERRAM_BIST": "1", "CYNTHION_HYPERRAM_BIST_DQS": "0"}
MIRROR = {"CYNTHION_CLOCK_MIRROR": "1"}


def test_different_variants_get_different_directories():
    dirs = {variant.build_dir(ROOT, env)
            for env in (DEFAULT, BIST, BIST_CK120, BIST_NODQS, MIRROR)}
    assert len(dirs) == 5, sorted(str(d) for d in dirs)


def test_the_same_variant_gets_the_same_directory():
    # Unset, set to the default, and set to a synonym of the default are one
    # variant: they elaborate identically. A directory that differed would cost
    # a resynthesis for nothing, and a cache key that differed used to refuse a
    # correct `--firmware-only` load as stale.
    same = [
        {},
        {"CYNTHION_HYPERRAM_BIST": ""},
        {"CYNTHION_HYPERRAM_BIST": "0"},
        # The DQS default, spelled out. Taken from the table rather than typed:
        # it is per path now, and a literal here would pin one of the two.
        {"CYNTHION_HYPERRAM_CK_MHZ": variant.CK_DEFAULT_MHZ[True],
         "CYNTHION_CLOCK_MIRROR_DIV": "4"},
    ]
    dirs = {variant.build_dir(ROOT, env) for env in same}
    assert len(dirs) == 1, sorted(str(d) for d in dirs)


def test_the_ck_default_follows_the_path():
    """One default could not be right for both: `hr` is CK, or CK/2 (#410)."""
    for dqs, expected in ((True, variant.CK_DEFAULT_MHZ[True]),
                          (False, variant.CK_DEFAULT_MHZ[False])):
        env = {"CYNTHION_HYPERRAM_BIST": "1",
               "CYNTHION_HYPERRAM_BIST_DQS": "1" if dqs else "0"}
        assert variant.value("CYNTHION_HYPERRAM_CK_MHZ", env) == expected
        # And an explicit value still wins on either path.
        assert variant.value("CYNTHION_HYPERRAM_CK_MHZ",
                             {**env, "CYNTHION_HYPERRAM_CK_MHZ": "120"}) == "120"


def test_a_ck_rung_is_its_own_directory():
    # The ladder's whole point: twenty rungs are twenty directories, so twenty
    # builds can be in flight at once.
    rungs = {variant.build_dir(ROOT, {"CYNTHION_HYPERRAM_BIST": "1",
                                      "CYNTHION_HYPERRAM_CK_MHZ": f"{ck}"})
             for ck in range(80, 100)}
    assert len(rungs) == 20


def test_the_directory_is_under_the_build_root_and_is_one_component():
    for env in (DEFAULT, BIST_CK120, {"CYNTHION_HYPERRAM_CK_MHZ": "85.7143,100"}):
        path = variant.build_dir(ROOT, env)
        assert path.parent == ROOT / "tmp" / "awto_soc" / "build"
        # A comma or a slash arriving from the environment must not create a
        # directory somewhere else, or split one name into two.
        assert re.fullmatch(r"[A-Za-z0-9._-]+", path.name), path.name


def test_the_directory_and_the_cache_key_separate_the_same_things():
    import soc_run
    for env in (DEFAULT, BIST, BIST_CK120, BIST_NODQS, MIRROR):
        assert soc_run.variant_settings.__module__  # imported, not shadowed
    # Every entry in the table appears in the slug and in the settings list.
    for name, _default, tag, _kind in variant.VARIANT_ENV:
        assert tag in variant.slug(), name
        assert any(s.startswith(f"{name}=") for s in variant.settings()), name
    assert soc_run.VARIANT_ENV is variant.VARIANT_ENV


# Variables top.py may read directly, because they cannot change the bitstream.
#
# A REFUSAL OVERRIDE only turns a refusal into the build that was already asked
# for -- same CK, same design, same digest -- so it does not divide variants and
# putting it in VARIANT_ENV would give every build a directory tag for a knob it
# did not use. Anything that changes what elaborates does NOT belong here.
REFUSAL_OVERRIDES = {
    "CYNTHION_HYPERRAM_CK_CEILING_MHZ",   # #410, mirrors clocks.py SYNC_CEILING
}


def test_top_py_reads_every_variant_variable_through_the_table():
    # An `os.environ.get` in top.py that is not in VARIANT_ENV is a bitstream
    # that hashes the same as a different one and shares its directory --
    # `CYNTHION_CLOCK_MIRROR` was exactly that until #351.
    source = TOP.read_text()
    stray = [name for name in
             re.findall(r'os\.environ\.get\(\s*"(CYNTHION_[A-Z_]+)"', source)
             if name not in REFUSAL_OVERRIDES]
    assert stray == [], stray
    for name, _default, _tag, _kind in variant.VARIANT_ENV:
        assert f'variant.flag("{name}")' in source or \
               f'variant.value("{name}")' in source, name


def test_an_unlisted_variable_is_refused():
    try:
        variant.flag("CYNTHION_NOT_IN_THE_TABLE")
    except KeyError:
        return
    raise AssertionError("an unlisted variable resolved instead of raising")


def test_the_build_directory_top_py_uses_is_the_one_soc_run_expects():
    # Both sides asked in a subprocess, with the same environment, because this
    # is the agreement that matters: `soc_run.py` looks for the bitstream where
    # `top.py` put it. Answered by import, so no build runs.
    env = {"CYNTHION_HYPERRAM_BIST": "1", "CYNTHION_HYPERRAM_CK_MHZ": "93.75"}
    from_soc_run = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'scripts');"
         "import soc_run; print(soc_run.BUILD)"],
        cwd=ROOT, capture_output=True, text=True, env={**_environ(), **env})
    assert from_soc_run.returncode == 0, from_soc_run.stderr[-800:]
    want = variant.build_dir(ROOT, env)
    assert from_soc_run.stdout.strip() == str(want), from_soc_run.stdout


def _environ():
    import os
    # Without the variant variables the parent happens to hold, so the child
    # sees exactly the variant this test names.
    return {k: v for k, v in os.environ.items()
            if k not in {n for n, _d, _t, _k in variant.VARIANT_ENV}}
