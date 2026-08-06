"""Contract tests for check.py's orphan-log pruning.

The pruner deletes files, and the one thing it must never do is delete the log
of a check that still exists -- that would throw away the output of a run
someone is about to read. These pin both halves: the retired check's log goes,
the live check's log stays.

Run: pytest tests/test_check_orphan_logs.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK_PY = REPO / "scripts" / "check.py"

pytestmark = pytest.mark.skipif(not CHECK_PY.exists(), reason="no scripts/check.py")


@pytest.fixture(scope="module")
def check_module():
    """Import check.py by path; scripts/ is not a package."""
    sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location("check_under_test", CHECK_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def logs_dir(check_module, tmp_path, monkeypatch):
    """Point the pruner at a scratch directory, never the real tmp/logs."""
    monkeypatch.setattr(check_module, "LOGS", tmp_path)
    return tmp_path


def test_removes_log_of_a_check_that_no_longer_exists(check_module, logs_dir):
    (logs_dir / "check-gateware.log").write_text("output of a deleted check\n")

    removed = check_module.prune_orphan_logs(["rust", "python"])

    assert removed == ["check-gateware.log"]
    assert not (logs_dir / "check-gateware.log").exists()


def test_keeps_log_of_a_registered_check(check_module, logs_dir):
    live = logs_dir / "check-rust.log"
    live.write_text("cargo output someone is about to read\n")

    removed = check_module.prune_orphan_logs(["rust", "python"])

    assert removed == []
    assert live.read_text() == "cargo output someone is about to read\n"


def test_keeps_logs_of_checks_not_selected_this_run(check_module, logs_dir):
    """`check.py rust` must not treat every other check as retired.

    The pruner takes the full registered set, so this is a test of the caller's
    contract as much as the function's: pass `names`, never `selected`.
    """
    for name in ("rust", "python", "apollo"):
        (logs_dir / f"check-{name}.log").write_text(name)

    removed = check_module.prune_orphan_logs(["rust", "python", "apollo"])

    assert removed == []
    assert len(list(logs_dir.glob("check-*.log"))) == 3


def test_leaves_unrelated_files_alone(check_module, logs_dir):
    """Only check-<name>.log is owned by this scheme.

    check.log in particular is the run log, not a per-check log, and it does
    not match check-*.log -- but a future glob loosened to check*.log would
    silently start eating it.
    """
    (logs_dir / "check.log").write_text("run log")
    (logs_dir / "dev.log").write_text("dev log")
    (logs_dir / "soc_test.log").write_text("some other script")

    removed = check_module.prune_orphan_logs(["rust"])

    assert removed == []
    assert (logs_dir / "check.log").exists()
    assert (logs_dir / "dev.log").exists()
    assert (logs_dir / "soc_test.log").exists()


def test_missing_logs_dir_is_not_an_error(check_module, tmp_path, monkeypatch):
    monkeypatch.setattr(check_module, "LOGS", tmp_path / "never-created")

    assert check_module.prune_orphan_logs(["rust"]) == []


def test_pruner_is_wired_into_the_runner():
    """A pruner nothing calls is the same bug it was written to fix."""
    source = CHECK_PY.read_text()
    body = source.split("def main(")[1]
    assert "prune_orphan_logs(names)" in body, \
        "main() must call prune_orphan_logs(names) -- not `selected`, which " \
        "would delete the logs of every check not named on the command line"
