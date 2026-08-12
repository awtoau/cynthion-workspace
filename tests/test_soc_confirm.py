#!/usr/bin/env python3
#
# Each of #360's three causes must be named, not lumped into "not answering".
# SPDX-License-Identifier: BSD-3-Clause

"""Does the post-configure confirmation name WHICH failure it is?

`apollo configure` exits 0 with no design running from three different causes
with one symptom (#360). A check that reports "board not answering" is what we
already had; these pin that each cause produces its own verdict.

Evidence is injected rather than measured. The board can hold exactly one of
these states at a time, so a test that waits for real hardware can only ever
cover one of the three -- and the whole defect is that they are indistinguishable
from each other, which is a property of the DECISION and not of the board.

`soc_confirm.diagnose` is pure for that reason: the probes fill an `Evidence`,
and this drives the same function they do.

The positive control is here too. A confirmation step that always passes is the
exact fault it exists to prevent, so `ok` must be reachable and must NOT be
reached by any of the failing sets.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))

import soc_confirm  # noqa: E402

DONE = soc_confirm.STATUS_DONE

# A status register as read off the real part on 2026-08-10 with a design
# loaded: DONE plus STANDARD_PRE. The blank form is the same with DONE cleared.
STATUS_RUNNING = 0x0020_0100
STATUS_BLANK = STATUS_RUNNING & ~DONE
ECP5_IDCODE = 0x2111_1043


def verdict(**evidence):
    return soc_confirm.diagnose(soc_confirm.Evidence(**evidence))


def test_a_reply_is_the_only_thing_that_passes():
    """The positive control: `ok` is reachable, and only via an answer."""
    assert verdict(apollo=True, console_usb=True, console_node="/dev/ttyACM4",
                   reply=b"\r\n> ").name == "ok"


def test_blank_fpga_is_named():
    """Cause 1: exit 0, FPGA blank, nothing enumerated. DONE is clear."""
    result = verdict(apollo=True, console_usb=False, idcode=ECP5_IDCODE,
                     status=STATUS_BLANK)
    assert result.name == "blank-fpga"
    assert "DONE clear" in " ".join(result.lines)


def test_stolen_port_names_the_holding_process():
    """Cause 2: a console service holds the tty without serving it.

    The pid and the command line are the point -- `soc_shell.py` already sets
    that standard, and "the board is not answering" does not meet it.
    """
    result = verdict(apollo=True, console_usb=True, console_node="/dev/ttyACM4",
                     reply=b"", thieves=[(4242, "python3 scripts/tio_user.py")])
    assert result.name == "stolen-port"
    assert "4242" in " ".join(result.lines)
    assert "tio_user.py" in " ".join(result.lines)


def test_wrong_port_is_named_from_the_done_bit():
    """Cause 3: Apollo is on CONTROL, the console is 1d50:6180 on AUX.

    A design is running -- the part says so -- and its console is on no bus.
    """
    result = verdict(apollo=True, console_usb=False, idcode=ECP5_IDCODE,
                     status=STATUS_RUNNING)
    assert result.name == "wrong-port"
    assert "AUX" in " ".join(result.lines)


def test_wrong_port_is_also_named_when_the_jtag_pin_uart_answers():
    """The second, independent route to the same verdict: the design speaks."""
    result = verdict(apollo=True, console_usb=False, jtag_console=b"\r\n> ")
    assert result.name == "wrong-port"


def test_blank_and_wrong_port_differ_only_in_the_done_bit():
    """The discriminator, stated as a test: same evidence, one bit apart."""
    common = dict(apollo=True, console_usb=False, idcode=ECP5_IDCODE)
    assert verdict(status=STATUS_RUNNING, **common).name == "wrong-port"
    assert verdict(status=STATUS_RUNNING & ~DONE, **common).name == "blank-fpga"


def test_an_absent_board_is_not_a_blank_fpga():
    """`soc_run.py` refuses to configure an absent board. Pinned here."""
    assert verdict(apollo=False, console_usb=False).name == "board-absent"


def test_the_console_without_apollo_is_a_missing_control_cable():
    blocked = soc_confirm.Verdict("control-unplugged", "", [])
    assert blocked.name == "control-unplugged"      # the name precheck() returns
    # And the same shape through diagnose: silence on a bound tty with no thief
    # is the firmware's own, not a cable.
    assert verdict(apollo=False, console_usb=True, console_node="/dev/ttyACM4",
                   reply=b"").name == "silent-firmware"


def test_jtag_contention_is_inconclusive_rather_than_a_verdict():
    """`ffffffff` is a design holding the bus OR a wedged part. Never guess."""
    result = verdict(apollo=True, console_usb=False, idcode=0xFFFF_FFFF)
    assert result.name == "jtag-contended"
    assert result.retry


def test_an_unbound_tty_is_retryable_not_a_fault():
    result = verdict(apollo=True, console_usb=True, console_node=None, reply=b"")
    assert result.name == "tty-unbound"
    assert result.retry


def test_silence_with_everything_present_is_the_firmware():
    """The residual, and the only verdict that blames the change under test."""
    result = verdict(apollo=True, console_usb=True, console_node="/dev/ttyACM4",
                     reply=b"", thieves=[])
    assert result.name == "silent-firmware"


def test_no_failing_evidence_ever_reports_ok():
    """The fault this whole step exists to prevent, asserted directly."""
    failing = [
        dict(apollo=False, console_usb=False),
        dict(apollo=True, console_usb=False, idcode=ECP5_IDCODE,
             status=STATUS_BLANK),
        dict(apollo=True, console_usb=False, idcode=ECP5_IDCODE,
             status=STATUS_RUNNING),
        dict(apollo=True, console_usb=True, console_node="/dev/ttyACM4",
             reply=b"", thieves=[(1, "x")]),
        dict(apollo=True, console_usb=True, console_node="/dev/ttyACM4",
             reply=b""),
        dict(apollo=True, console_usb=True, console_node=None, reply=b""),
        dict(apollo=True, console_usb=False, idcode=0),
        # Whitespace is not an answer: a CR echoed back by a driver is not the
        # shell speaking, and treating it as one is how a check starts passing
        # unconditionally.
        dict(apollo=True, console_usb=True, console_node="/dev/ttyACM4",
             reply=b"\r\n \t"),
    ]
    for evidence in failing:
        assert not verdict(**evidence).ok, evidence


def test_other_readers_names_a_real_process_holding_a_real_tty():
    """The thief detector against a live pty, not a fixture.

    `stolen-port` is only worth anything if the /proc walk finds the holder, so
    this holds one for real and asserts the pid and command line come back.
    """
    from soc_shell import other_readers

    primary, replica = os.openpty()
    node = os.ttyname(replica)
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import sys; f=open({node!r}); sys.stdout.write('held\\n'); "
         f"sys.stdout.flush(); f.read()"],
        stdout=subprocess.PIPE, text=True)
    try:
        # Waits for: the child's own "held" line, which it prints after the open
        # succeeds. A condition, not a duration -- there is nothing to sleep for.
        assert holder.stdout.readline().strip() == "held"
        found = other_readers(node)
        assert any(pid == holder.pid for pid, _ in found), found
        assert any("python" in command for _, command in found), found
    finally:
        holder.kill()
        holder.wait()
        os.close(primary)
        os.close(replica)


def test_a_bitstream_with_no_console_is_confirmed_by_done_alone():
    """A JTAG-readback probe has no shell; asking for one would score it dead.

    `hyperram_identify.py` reads its results over JTAG registers. DONE is the
    whole of the confirmation available, and it is still more than an exit code.
    """
    assert verdict(expect="design", apollo=True, idcode=ECP5_IDCODE,
                   status=STATUS_RUNNING).name == "ok"
    assert verdict(expect="design", apollo=True, idcode=ECP5_IDCODE,
                   status=STATUS_BLANK).name == "blank-fpga"
    assert verdict(expect="design", apollo=False).name == "board-absent"


def test_a_console_less_bitstream_is_never_called_wrong_port():
    """`wrong-port` is about a console that should have enumerated. There is none."""
    assert verdict(expect="design", apollo=True, console_usb=False,
                   idcode=ECP5_IDCODE, status=STATUS_RUNNING).ok


def test_expect_console_passes_on_enumeration_without_prompting():
    """For callers that capture the banner themselves.

    The banner is flushed on the FIRST received byte, so a prompt here would eat
    the transcript `riscv_console_capture.py` exists to record. Presence of
    1d50:6180 is the confirmation, and a blank FPGA still cannot fake it.
    """
    assert verdict(expect="console", apollo=True, console_usb=True).name == "ok"
    assert verdict(expect="console", apollo=True, console_usb=False,
                   idcode=ECP5_IDCODE, status=STATUS_BLANK).name == "blank-fpga"
    assert verdict(expect="console", apollo=True, console_usb=False,
                   idcode=ECP5_IDCODE, status=STATUS_RUNNING).name == "wrong-port"


def test_the_cli_names_a_stolen_port_end_to_end():
    """The whole path -- probe, diagnose, report -- against a real held tty.

    No board needed: a pty stands in for the console node, and the holder is a
    real process the /proc walk has to find. Costs one console budget
    (2 x CONSOLE_REPLY_S) because it waits out the silence it is diagnosing.
    """
    import json

    primary, replica = os.openpty()
    node = os.ttyname(replica)
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import sys; f=open({node!r}); sys.stdout.write('held\\n'); "
         f"sys.stdout.flush(); f.read()"],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "soc_confirm.py"),
             "--node", node, "--json"],
            capture_output=True, text=True, cwd=ROOT)
        assert result.returncode == 1, result.stdout
        report = json.loads(result.stdout)
        assert report["verdict"] == "stolen-port"
        assert any(entry["pid"] == holder.pid
                   for entry in report["evidence"]["thieves"]), report
    finally:
        holder.kill()
        holder.wait()
        os.close(primary)
        os.close(replica)


def test_every_verdict_the_module_can_return_is_covered():
    """A new verdict must arrive with a test, or this fails and names it.

    Across the whole suite, not this file alone: the identity verdicts are
    driven from `test_usercode_stale_load.py`, which owns their controls.
    """
    produced = set(re.findall(r'Verdict\("([a-z-]+)"',
                              (ROOT / "scripts" / "soc_confirm.py").read_text()))
    covered = set()
    for test in sorted(Path(__file__).parent.glob("test_*.py")):
        covered |= set(re.findall(r'== "([a-z-]+)"', test.read_text()))
    assert not produced - covered, f"verdicts with no test: {sorted(produced - covered)}"
