#!/usr/bin/env python3
#
# The arbiter must not become the twelfth instrument that cannot fail. #430
# SPDX-License-Identifier: BSD-3-Clause

"""What a transcript may and may not claim, and how the queue orders jobs.

Every test here is a NEGATIVE CONTROL or an ordering rule. Nothing touches the
board: `Arbiter.verdict` and `make_job` are pure, and the queue is exercised
through its own methods.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))

import board_arbiter as arb  # noqa: E402


def record(*, confirm="ok", image="deadbee", transcript=(), kind="run"):
    return {"kind": kind, "transcript": list(transcript),
            "provenance": {"confirm": {"verdict": confirm},
                           "board": {"image": image}}}


def job(kind="run", commands=("bist smoke",), priority="normal"):
    return arb.Job(id="test", kind=kind, commands=list(commands),
                   priority=priority)


# --- a transcript may not look like a pass when nothing ran ----------------


def test_a_run_with_no_commands_cannot_pass():
    """The whole failure mode this repo keeps finding: exit 0 having done
    nothing. A run job with an empty transcript is a failure, not a pass."""
    assert arb.Arbiter.verdict(job(), record()) == "failed"


def test_no_commands_is_refused_at_submission():
    with pytest.raises(arb.Refused):
        arb.make_job({"kind": "run", "commands": []})
    with pytest.raises(arb.Refused):
        arb.make_job({"kind": "shell", "commands": []})


def test_a_failed_confirm_cannot_pass():
    """`apollo configure` returning 0 is not a running design (#360)."""
    steps = [{"command": "bist smoke", "status": "ok"}]
    assert arb.Arbiter.verdict(
        job(), record(confirm="blank-fpga", transcript=steps)) == "failed"


def test_a_board_that_reported_no_image_cannot_pass():
    """Without the board's own commit the rows belong to no build (#367)."""
    steps = [{"command": "bist smoke", "status": "ok"}]
    assert arb.Arbiter.verdict(
        job(), record(image=None, transcript=steps)) == "failed"


def test_a_command_without_a_prompt_cannot_pass():
    steps = [{"command": "bist all 8", "status": "timeout"}]
    assert arb.Arbiter.verdict(job(), record(transcript=steps)) == "failed"


def test_a_preempted_job_is_never_a_pass():
    steps = [{"command": "bist all 8", "status": "preempted"}]
    assert arb.Arbiter.verdict(job(), record(transcript=steps)) == "preempted"


def test_the_whole_sequence_passes_when_every_leg_did():
    steps = [{"command": "bist smoke", "status": "ok"}]
    assert arb.Arbiter.verdict(job(), record(transcript=steps)) == "passed"


def test_a_configure_job_passes_without_commands():
    """`configure` runs no commands by definition; the rule is per kind."""
    assert arb.Arbiter.verdict(job(kind="configure", commands=()),
                               record(kind="configure")) == "passed"


# --- what may be asked for -------------------------------------------------


def test_a_shell_job_may_not_claim_a_variant():
    """It does not configure, so a variant would be provenance it never checked."""
    with pytest.raises(arb.Refused):
        arb.make_job({"kind": "shell", "commands": ["info"], "variant": "x"})


def test_an_unbuilt_variant_is_refused_with_what_is_built():
    with pytest.raises(arb.Refused) as failure:
        arb.resolve_bitstream(arb.Job(id="t", variant="no-such-variant"))
    assert "no-such-variant" in str(failure.value)
    assert "Built:" in str(failure.value)


def test_a_missing_bitstream_is_refused():
    with pytest.raises(arb.Refused):
        arb.resolve_bitstream(arb.Job(id="t", bitstream="tmp/not-a-file.bit"))


def test_unknown_kinds_and_priorities_are_refused():
    with pytest.raises(arb.Refused):
        arb.make_job({"kind": "flash", "commands": ["x"]})
    with pytest.raises(arb.Refused):
        arb.make_job({"kind": "run", "commands": ["x"], "priority": "urgent"})


# --- the queue -------------------------------------------------------------


class Bare(arb.Arbiter):
    """The queue without the directories, so ordering is testable anywhere."""

    def __init__(self):
        import threading

        self.lock = threading.Condition()
        self.normal, self.idle, self.by_id = arb.deque(), arb.deque(), {}
        self.running = None
        self.preempt = threading.Event()
        self.board = arb.Board()
        self.stopping = False
        self.started = arb.now()


def test_a_normal_job_is_taken_before_an_idle_one():
    queue = Bare()
    queue.submit(arb.Job(id="idle-1", priority="idle", commands=["bist smoke"]))
    queue.submit(arb.Job(id="real-1", commands=["bist smoke"]))
    assert queue.take().id == "real-1"


def test_a_normal_submission_preempts_a_running_idle_job():
    queue = Bare()
    queue.submit(arb.Job(id="idle-1", priority="idle", commands=["bist all 8"]))
    queue.take()                                    # the idle job is running
    assert not queue.preempt.is_set()
    queue.submit(arb.Job(id="real-1", commands=["info"]))
    assert queue.preempt.is_set()


def test_an_idle_submission_does_not_preempt_another_idle_job():
    queue = Bare()
    queue.submit(arb.Job(id="idle-1", priority="idle", commands=["bist all 8"]))
    queue.take()
    queue.submit(arb.Job(id="idle-2", priority="idle", commands=["bist smoke"]))
    assert not queue.preempt.is_set()


def test_two_jobs_queue_rather_than_collide():
    queue = Bare()
    queue.submit(arb.Job(id="a", commands=["info"]))
    queue.submit(arb.Job(id="b", commands=["info"]))
    assert [job.id for job in queue.normal] == ["a", "b"]
    assert queue.take().id == "a"
    assert [job.id for job in queue.normal] == ["b"]


# --- board state -----------------------------------------------------------


def test_an_unknown_board_is_never_assumed_to_hold_the_right_image():
    """A cold start knows nothing, so the first job of any variant configures."""
    assert arb.Board().loaded is None


def test_a_polluted_console_refuses_a_shell_job():
    """After a preemption the board may still be printing another job's sweep.

    `guard` is stubbed: this is about the pollution rule, and the test must hold
    on a machine with no board attached.
    """
    queue = Bare()
    queue.board.polluted = True
    queue.board.guard = lambda: None
    with pytest.raises(arb.Refused) as failure:
        queue.prepare(job(kind="shell", commands=["info"]))
    assert "preempted" in str(failure.value)


def test_a_board_held_by_someone_else_is_refused_by_name():
    """Going around the arbiter is detectable, and the holder is named."""
    board = arb.Board()
    board.foreign_holders = lambda: [(4242, "python3 scripts/soc_run.py")]
    with pytest.raises(arb.Refused) as failure:
        board.guard()
    assert "4242" in str(failure.value)
    assert "soc_run.py" in str(failure.value)
