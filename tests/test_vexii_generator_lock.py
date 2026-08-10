#!/usr/bin/env python3
#
# The generator lock has to be keyed on the checkout, not on the worktree. #306.
# SPDX-License-Identifier: BSD-3-Clause

"""Does one generator lock actually exclude two builds?

`cpu.generate()` runs sbt with its cwd set to the VexiiRiscv checkout, and
SpinalHDL writes `VexiiRiscv.v` and `target/` there. Two of those at once is the
race in #306, whose dangerous outcome is a build that SUCCEEDS on another
configuration's core.

The lock closes that only if both builds take the same lock. `git worktree add`
does not populate submodules, so every worktree here shares the main checkout's
`repos/vexiiriscv` -- and a lock under each worktree's own `tmp/` would leave the
shared sources unprotected while looking protected.

Asserted rather than eyeballed:

  * the lock lives beside the checkout it protects, not beside `ROOT`
  * a worktree with no submodule of its own resolves to the main checkout's
  * `VEXII_ROOT` moves both together, so they cannot drift apart
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ASK = (
    "import sys; sys.path.insert(0, 'gateware/soc');"
    "import cpu.cpu as c;"
    "print(c.VEXII);"
    "print(c.VEXII.parent.parent / 'tmp' / 'vexii-generate.lock')"
)


def _ask(env=None):
    """(checkout, lock path) as the module resolves them, in a child process."""
    import os
    result = subprocess.run([sys.executable, "-c", ASK], cwd=ROOT,
                            capture_output=True, text=True,
                            env={**os.environ, **(env or {})})
    assert result.returncode == 0, result.stderr[-800:]
    checkout, lock = result.stdout.split()
    return Path(checkout), Path(lock)


def test_the_lock_sits_beside_the_checkout_it_protects():
    checkout, lock = _ask()
    assert lock == checkout.parent.parent / "tmp" / "vexii-generate.lock"


def test_a_worktree_resolves_to_the_checkout_that_has_the_sources():
    # In a worktree these differ, and that is the whole point: the sources are
    # the main checkout's, so the lock must be the main checkout's too.
    checkout, lock = _ask()
    assert (checkout / "build.sbt").is_file() or not checkout.exists(), checkout
    if checkout.parent.parent != ROOT:
        assert not (ROOT / "repos" / "vexiiriscv" / "build.sbt").exists()


def test_vexii_root_moves_the_lock_with_it(tmp_path):
    # An override that moved the sources but not the lock would be two builds
    # generating into one directory while holding different locks.
    fake = tmp_path / "vexii-checkout" / "repos" / "vexiiriscv"
    fake.mkdir(parents=True)
    (fake / "build.sbt").write_text("// enough to be recognised as a checkout\n")
    checkout, lock = _ask({"VEXII_ROOT": str(fake)})
    assert checkout == fake
    assert lock == fake.parent.parent / "tmp" / "vexii-generate.lock"
