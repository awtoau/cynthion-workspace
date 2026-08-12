#!/usr/bin/env python3
#
# The step between "apollo configure returned" and "take a measurement". See #360.
# SPDX-License-Identifier: BSD-3-Clause

"""
Confirm a design is actually running, and NAME which failure it is when it is not.

    ./scripts/soc_confirm.py                # confirm; exit 1 and name the cause
    ./scripts/soc_confirm.py --json         # the same, machine-readable
    ./scripts/soc_confirm.py --before       # the pre-configure gate only
    ./scripts/soc_confirm.py --expect design   # no console: the DONE bit only

    from soc_confirm import configure_and_confirm     # configure, then confirm

## Why

`apollo configure` exits 0 with no design running -- four sessions on 2026-08-10,
~15 min each, from THREE different causes with ONE symptom (#360):

- the FPGA left blank: exit 0, nothing enumerated
- a console service holding the tty without serving it: every read empty
- the cable in the wrong socket: Apollo is CONTROL, the console is `1d50:6180`
  on AUX (`gateware/usb_ids.py`)

Each time the first hypothesis was that the change under test had killed the
board. A generic "board not answering" is what we already had; naming the cause
is the whole value.

## Verdicts

| verdict             | means                                        | decided by |
|---------------------|----------------------------------------------|------------|
| `ok`                | the shell answered                            | a reply to a CR |
| `blank-fpga`        | configure left no design                      | ECP5 on the chain, status DONE clear |
| `stolen-port`       | another process is eating the console         | /proc holders of the tty, named |
| `wrong-port`        | design running, its console is on no bus      | DONE set (or the JTAG-pin UART answers) with `1d50:6180` absent |
| `control-unplugged` | console on the bus, Apollo is not             | `6180` present, `615c` absent |
| `board-absent`      | nothing of this board is on USB               | neither ID present |
| `tty-unbound`       | device enumerated, no tty yet                 | USB present, no node -- retryable |
| `jtag-contended`    | the ECP5 will not answer JTAG                 | IDCODE `ffffffff`/`0` -- retryable, inconclusive |
| `silent-firmware`   | everything present and free, no reply         | the residual, and the only one that IS the firmware |
| `wrong-part`        | this is not the die the build was packed for  | IDCODE against the build record's, from prjtrellis |
| `stale-bitstream`   | a design is running and it is not this build  | USERCODE against the build record's |
| `declared-clock`    | the build asks for a clock this part cannot do | the record's `sync_hz`/`usb_hz` against `soc/clocks.py` |

The last three need an EXPECTATION -- the `usercode.json` the gateware build
writes beside its bitstream (`gateware/usercode_map.py`). Without one they are
not decided either way, and `confirm()` says so rather than passing.

## What it cannot distinguish

- `wrong-port` covers three physical states with one signature: the AUX cable
  unplugged, the AUX cable in TARGET, and a bitstream that presents on a port
  other than the one asked for. All read as "a design is running, `6180` is not
  on the bus". The message lists all three.
- `stolen-port` sees only holders in this PID namespace; a container's reader is
  invisible and would score as `silent-firmware`.
- `jtag-contended` is deliberately inconclusive: a design holding the shared JTAG
  bus and a wedged part both read back `ffffffff`.
- `wrong-part` and `stale-bitstream` are only as good as the record beside the
  bitstream. A build directory with no `usercode.json` leaves both undecided.
- `declared-clock` is about what the build ASKED FOR, not what the board runs at.
  Counting `sync` needs the ClockMonitor and the CPU; `info` does that, and the
  two checks are from opposite directions.
- `--expect design` stops at DONE: a probe read over JTAG registers presents no
  console, so "running" is as far as the evidence goes. `--expect console`
  requires the console to enumerate without prompting it, for callers that
  capture the banner themselves -- the first received byte flushes it, so a
  prompt here would eat the transcript they are about to read.

## Order of evidence, and why the console goes first

The console is asked before anything touches JTAG, so a HEALTHY board is never
disturbed by the confirmation: `JTAG_START` drives the shared pins the design's
second UART sits on. Nothing below the console probe runs unless the console has
already failed to answer.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit, log  # noqa: E402

CLI = Path("repos") / "apollo" / "apollo_fpga" / "commands" / "cli.py"


def apollo_cli():
    """Apollo's CLI, which a git worktree may not have a copy of.

    Submodules live in the main working tree only, so a run from
    `.claude/worktrees/...` can find no `repos/` at all. `--git-common-dir` names
    the shared `.git`, whose parent is that tree.
    """
    if (ROOT / CLI).exists():
        return ROOT / CLI
    common = subprocess.run(("git", "rev-parse", "--path-format=absolute",
                             "--git-common-dir"), cwd=ROOT,
                            capture_output=True, text=True, check=True)
    main = Path(common.stdout.strip()).parent / CLI
    if not main.exists():
        raise SystemExit(f"no apollo CLI at {ROOT / CLI} or {main}")
    return main

VENDOR_ID = 0x1d50
# Apollo on CONTROL. `gateware/usb_ids.py` reserves it and owns every other number.
APOLLO_PID = 0x615c
# The RISC-V console on AUX -- 0x6180, by name so the number lives in one file.
CONSOLE = "riscv_console"

# ECP5 configuration status bits, from repos/apollo/apollo_fpga/ecp5.py.
# DONE is the part's own statement that a design is loaded and running.
STATUS_DONE = 1 << 8
STATUS_SPI_FAIL = 1 << 22

# How long the shell gets to answer a CR.
#
# Waits for: a prompt. Expected: milliseconds over USB bulk; ~1.5 s worst case is
# enumeration plus the banner flush. 1.3x that. On expiry: the read returns what
# arrived and the verdict below names the cause -- silence is never reported bare.
CONSOLE_REPLY_S = 2.0

# How long the AUX console gets to come BACK on the bus after a configure.
#
# Waits for: 1d50:6180 re-enumerating. Expected: configuring drops the device and
# the host rebinds it in ~1.3 s measured on this board (#419). 2.5 s is ~1.9x
# that, because enumeration competes with whatever else is on the bus. On expiry:
# say so with the elapsed and confirm ANYWAY -- the wait removes a false
# `wrong-port`, it never turns a dead board into a pass.
CONSOLE_ENUMERATE_S = 2.5

# How often that wait looks. `udevadm settle` returns instantly on an idle queue,
# so a bare loop around it spins a core.
ENUMERATE_TICK_S = 0.05

# The FIRST command after a boot is swallowed: the banner is held in the log ring
# and flushed on the first received byte, so it interleaves with the echo. Two
# asks, not one -- one ask scores a live board dead (see hyperram_pin_axis.py).
CONSOLE_ASKS = 2

# How far confirmation can go, per bitstream. The caller says which, because
# only the caller knows what its bitstream presents and what it is about to read.
#
#   "shell"   the SoC console: prompted, so silence is attributable
#   "console" the console must ENUMERATE, and is not prompted -- for callers
#             that capture the banner themselves, which the first byte flushes
#   "design"  no console at all (a JTAG-register probe): DONE is the evidence
EXPECT = ("shell", "console", "design")

# `--json` keeps stdout to one parseable object. Set there and nowhere else.
QUIET = False


def say(message=""):
    """A progress line: stdout and the log, or log-only under `--json`.

    Nothing is ever dropped -- `tmp/logs/dev.log` gets the line either way, so a
    machine-readable run still leaves the same record a human one does.
    """
    if QUIET:
        log(message, "INFO", depth=3)
    else:
        emit(message)


@dataclass
class Evidence:
    """What was observed. `None` is "not looked at", which is not "absent"."""

    apollo: bool | None = None          # 1d50:615c on the bus
    console_usb: bool | None = None     # 1d50:6180 on the bus
    console_node: str | None = None     # its tty, once udev has bound one
    served: bool = False                # read through tio_user.py --serve on 9000
    thieves: list = field(default_factory=list)   # (pid, cmdline) holding the tty
    reply: bytes | None = None          # what the shell said to a CR
    idcode: int | None = None           # ECP5 IDCODE from the JTAG scan
    status: int | None = None           # ECP5 configuration status register
    usercode: int | None = None         # ECP5 USERCODE, opcode 0xc0
    # The `usercode.json` beside the bitstream that was just loaded. None is
    # "nothing to compare against", which is not "they agree".
    expected: dict | None = None
    jtag_console: bytes | None = None   # the JTAG-pin UART, Apollo's own CDC node
    # How far confirmation can go for THIS bitstream. See `EXPECT`.
    expect: str = "shell"

    def as_json(self):
        return {
            "expect": self.expect,
            "apollo": self.apollo, "console_usb": self.console_usb,
            "console_node": self.console_node, "served": self.served,
            "thieves": [{"pid": pid, "command": cmd} for pid, cmd in self.thieves],
            "reply": (self.reply or b"").decode("ascii", "replace") or None,
            "jtag_console": (self.jtag_console or b"").decode("ascii", "replace")
                            or None,
            "idcode": None if self.idcode is None else f"{self.idcode:08x}",
            "status": None if self.status is None else f"{self.status:08x}",
            "done": None if self.status is None else bool(self.status & STATUS_DONE),
            "usercode": (None if self.usercode is None
                         else f"{self.usercode:08x}"),
            "expected": self.expected,
        }


@dataclass
class Verdict:
    name: str
    headline: str
    lines: list
    retry: bool = False        # a retry could plausibly change the answer

    @property
    def ok(self):
        return self.name == "ok"


def from_jtag(ev: Evidence, when_running) -> Verdict:
    """The part's own account of itself. `when_running()` is what DONE set means.

    DONE set means a design is loaded -- whether that is success or `wrong-port`
    depends on what the caller expected to enumerate, which is the caller's to
    say and not this function's. A callable, because that verdict quotes a status
    register the other two branches do not have.
    """
    if ev.status is None or ev.idcode in (0, 0xFFFFFFFF):
        return Verdict("jtag-contended", "the ECP5 did not answer JTAG", [
            f"  IDCODE {'not read' if ev.idcode is None else f'{ev.idcode:08x}'}"
            f", status {'not read' if ev.status is None else f'{ev.status:08x}'}",
            "  A design holding the shared JTAG bus and a wedged part read back",
            "  the same. INCONCLUSIVE by construction -- retry, and if it repeats",
            "  it is not contention.",
        ], retry=True)

    if not ev.status & STATUS_DONE:
        lines = [
            f"  ECP5 {ev.idcode:08x} is on the chain, status {ev.status:08x},"
            f" DONE clear",
            "  The part itself says no design is loaded. `apollo configure`",
            "  returning 0 is not evidence against this -- see #360.",
        ]
        if ev.status & STATUS_SPI_FAIL:
            lines.append("  SPI_FAIL is set: it also could not boot from flash.")
        return Verdict("blank-fpga", "the FPGA is blank", lines)

    return when_running()


def from_identity(ev: Evidence):
    """Is this the right part running the right build? A Verdict, or None.

    None means "not decided here", which happens when there is no expectation to
    compare against or the part did not answer -- never when the two agree and
    never when they disagree.

    FIRST, before the console. A design that is running answers a prompt exactly
    as well when it is the wrong one, so `ok` from a reply would be a pass over
    the top of a stale load.
    """
    want = ev.expected
    if not want:
        return None

    # The part, before the bitstream. A different die makes every number taken
    # from this board a number about a different device, and it would otherwise
    # be reported as a stale bitstream -- which is the wrong repair.
    if want.get("idcode") is not None and ev.idcode not in (None, 0, 0xFFFFFFFF):
        if ev.idcode != want["idcode"]:
            return Verdict("wrong-part", "this is not the part the build was packed for", [
                f"  IDCODE {ev.idcode:08x}, the build wants {want['idcode']:08x} "
                f"({want.get('device')})",
                "  prjtrellis' devices.json is where the expected value comes from,",
                "  through the platform's own `device` string -- not typed here.",
                "  ecppack packed for one die and this is another: timing, resources",
                "  and every measurement from this board are about a different chip.",
            ])

    if ev.usercode is not None and ev.usercode != want.get("usercode"):
        found = None
        try:
            import usercode_map

            found = usercode_map.lookup(ev.usercode)
        except Exception:
            pass
        lines = [
            f"  USERCODE {ev.usercode:08x}, this build stamped "
            f"{want.get('usercode', 0):08x}",
            f"  wanted: {(want.get('commit') or '?')[:7]}"
            f"{' dirty' if want.get('dirty') else ''} {want.get('variant')}",
        ]
        if found:
            lines.append(f"  running: {(found.get('commit') or '?')[:7]}"
                         f"{' dirty' if found.get('dirty') else ''} "
                         f"{found.get('variant')} from {found.get('build_dir')}")
        else:
            lines.append("  running: no record for those 32 bits -- a build from "
                         "another tree, or one whose record was cleaned away")
        lines += [
            "  The board is configured with a bitstream that is not the one just",
            "  built. Every peripheral address, clock and geometry the firmware",
            "  assumes may be wrong, and nothing on the board would say so.",
        ]
        return Verdict("stale-bitstream", "a design is running and it is not this build",
                       lines)

    # What the build ASKED the PLL for, against what the part can do. Cheap, and
    # it is the one check that does not need the board to have answered.
    try:
        import usercode_map

        problems = usercode_map.clock_problems(want)
    except Exception:
        problems = []
    if problems:
        return Verdict("declared-clock", "this build declares a clock the part cannot deliver",
                       ["  " + line for line in problems] + [
                           "  Built for {} -{}, usercode {:08x}.".format(
                               want.get("device"), want.get("speed"),
                               want.get("usercode", 0)),
                       ])
    return None


def diagnose(ev: Evidence) -> Verdict:
    """The evidence to a named cause. Pure -- `tests/test_soc_confirm.py` drives it.

    Ordered by what is cheapest to be certain about: identity settles it outright,
    then a reply, then the bus census, and only then the JTAG evidence which is
    the least direct.
    """
    wrong = from_identity(ev)
    if wrong is not None:
        return wrong

    if ev.expect == "design":
        # A bitstream with no console -- a probe read over JTAG registers, say.
        # DONE is the whole of the evidence there is.
        if ev.apollo is False:
            return Verdict("board-absent", "no part of this board is on USB", [
                "  1d50:615c (Apollo, CONTROL) is not on the bus.",
            ])
        return from_jtag(ev, lambda: Verdict(
            "ok", "the part reports a configured design", [
                f"  status {ev.status:08x}, DONE set. This bitstream presents no",
                "  console, so DONE is as far as confirmation goes -- whether the",
                "  design does its job is the caller's own readback.",
            ]))

    if ev.expect == "console" and ev.console_usb:
        # Presence only, deliberately: the caller reads this console itself and
        # the banner is flushed on the FIRST received byte, so prompting here
        # would consume the transcript it is about to capture.
        return Verdict("ok", "the console enumerated", [
            "  1d50:6180 is on the bus. Not prompted -- the caller reads it.",
        ])

    if ev.reply and ev.reply.strip():
        return Verdict("ok", "the shell answered", [
            f"  {ev.reply.decode('ascii', 'replace').strip()[:200]!r}"])

    # Before the bus census: a named holder settles it whatever else is true, and
    # the list is only ever populated for a node this opened and read nothing from.
    if ev.thieves:
        return Verdict("stolen-port", "another process is reading the console", [
            f"  {ev.console_node} is open in {len(ev.thieves)} other process(es):",
            *[f"      pid {pid}: {command}" for pid, command in ev.thieves],
            "  The silence is contention, not a dead design. Stop it, or restart",
            "  it as `./scripts/tio_user.py --serve` so readers share the socket",
            "  on 9000 instead of competing for the tty.",
        ])

    if ev.console_usb:
        if not ev.console_node and not ev.served:
            return Verdict("tty-unbound", "the console is on USB but has no tty yet", [
                "  1d50:6180 enumerated; udev has bound no /dev/ttyACM* to it.",
                "  Transient after a reconfigure rather than a fault -- retry.",
            ], retry=True)
        return Verdict("silent-firmware", "the design is running and says nothing", [
            f"  1d50:6180 on the bus, read via "
            f"{'the service on 9000' if ev.served else ev.console_node}, "
            f"no other reader,",
            f"  no reply to {CONSOLE_ASKS} prompts of {CONSOLE_REPLY_S:.0f} s each.",
            "  This one IS the firmware: the console path is intact end to end.",
        ])

    # The console is not on the bus. Everything below decides WHY.
    if ev.apollo is False:
        return Verdict("board-absent", "no part of this board is on USB", [
            "  neither 1d50:615c (Apollo, CONTROL) nor 1d50:6180 (console, AUX)",
            "  Nothing was configured and nothing can be measured. Check power",
            "  and the CONTROL cable before blaming anything that was changed.",
        ])

    if ev.jtag_console and ev.jtag_console.strip():
        return Verdict("wrong-port", "a design is running; its console is on no bus", [
            "  the JTAG-pin UART (Apollo's own CDC node) answered, so the design",
            "  is alive -- but 1d50:6180 is absent, so nothing can read AUX:",
            "  the AUX cable is unplugged, in TARGET, or this bitstream presents",
            "  on another port. Apollo is CONTROL; the console is AUX.",
        ])

    return from_jtag(ev, lambda: Verdict(
        "wrong-port", "a design is running; its console is on no bus", [
            f"  status {ev.status:08x}, DONE set -- the part says a design is loaded",
            "  but 1d50:6180 is not on the bus. The AUX cable is unplugged, is in",
            "  TARGET, or this bitstream presents on another port. Apollo is",
            "  CONTROL; the console is AUX (gateware/usb_ids.py).",
        ]))


# ---------------------------------------------------------------------------
# Gathering the evidence
# ---------------------------------------------------------------------------


def usb_present(product_id):
    """Is `1d50:<product_id>` on the bus? None if pyusb cannot answer."""
    try:
        import usb.core
        return usb.core.find(idVendor=VENDOR_ID, idProduct=product_id) is not None
    except Exception:
        return None


def tty_for(vendor, product):
    """The /dev/ttyACM* node for a VID:PID, or None. Same sysfs walk as usb_ids."""
    import glob
    import os

    for node in sorted(glob.glob("/dev/ttyACM*")):
        path = os.path.realpath(f"/sys/class/tty/{os.path.basename(node)}/device")
        for _ in range(8):
            vendor_file = os.path.join(path, "idVendor")
            if os.path.exists(vendor_file):
                try:
                    vid = int(open(vendor_file).read().strip(), 16)
                    pid = int(open(os.path.join(path, "idProduct")).read().strip(), 16)
                except OSError:
                    break
                if (vid, pid) == (vendor, product):
                    return node
                break
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent
    return None


def service_socket():
    """A connection to `tio_user.py --serve` on 9000, or None.

    Waits for: a loopback connect, which completes in well under a millisecond.
    0.5 s is the floor for a loaded machine; on expiry there is no service and
    the tty is read directly.
    """
    import socket

    try:
        return socket.create_connection(("127.0.0.1", 9000), timeout=0.5)
    except OSError:
        return None


def prompt(link):
    """CR, twice, and whatever came back.

    Twice because the FIRST command after a boot is swallowed: the banner is held
    in the log ring and flushed on the first received byte, so it interleaves with
    the echo. One ask scores a live board dead (`hyperram_pin_axis.py`).
    """
    reply = b""
    try:
        for _ in range(CONSOLE_ASKS):
            link.write(b"\r")
            link.settle(0.05)
            reply = link.read_until_prompt(CONSOLE_REPLY_S)
            if reply.strip():
                break
    finally:
        link.close()
    return reply


def ask_console(ev, node=None):
    """Prompt the shell and record what it said. Fills `reply`, `served`, `thieves`."""
    from soc_shell import Link, other_readers

    target = node or ev.console_node
    sock = None if node else service_socket()
    if sock is not None:
        link, ev.served = Link(sock=sock, how="service on 9000"), True
    elif target:
        try:
            link = Link.open(target)
        except Exception as failure:
            say(f"  console: {target} will not open ({failure})")
            return
    else:
        # No tty and no service. Opening by name would spin `wait_for_tty` for
        # minutes on a device that is not there; `tty-unbound` says so in one line.
        return
    say(f"  console: {link.how}")
    ev.reply = prompt(link)
    # Only interesting when the answer was empty, and a reader that attached
    # mid-probe is just as fatal -- so it is checked after, not before. Skipped
    # when reading through the service: the tty holder is then the service doing
    # its job, and naming it would be an accusation against the wrong process.
    if not ev.reply.strip() and ev.console_node and not ev.served:
        ev.thieves = other_readers(ev.console_node)


def ask_jtag_console(ev):
    """Prompt the JTAG-pin UART on Apollo's own CDC node. Proof a design is alive.

    The firmware answers on every UART in `target::UART_BASES`; the second is on
    the shared JTAG pins and appears as Apollo's CDC node. It answers when AUX is
    unplugged, which is exactly what separates `wrong-port` from `blank-fpga`.
    """
    node = tty_for(VENDOR_ID, APOLLO_PID)
    if not node:
        return
    from soc_shell import Link

    try:
        link = Link.open(node)
    except Exception as failure:
        say(f"  JTAG-pin UART {node}: will not open ({failure})")
        return
    # The node's buffer holds what the design wrote before it stopped, and those
    # bytes read as a live reply -- a forced-offline FPGA scored `wrong-port`
    # ("a design is running") on that evidence alone.
    link.discard_pending()
    ev.jtag_console = prompt(link)
    say(f"  JTAG-pin UART {node}: "
        f"{'answered' if ev.jtag_console.strip() else 'silent'}")


def ask_jtag(ev):
    """IDCODE and the ECP5 configuration status. Read-only: no ISC_ENABLE, no erase.

    Last, and only when the console has already failed: `JTAG_START` drives the
    pins the design's second UART uses, so a healthy board is never poked.
    """
    try:
        import apollo_fpga
    except ImportError:
        return
    try:
        board = apollo_fpga.ApolloDebugger()
    except Exception as failure:
        say(f"  apollo: {failure}")
        return
    try:
        with board.jtag as chain:
            codes = chain.enumerate(return_idcodes=True)
            ev.idcode = codes[0] if codes else 0
            if ev.idcode not in (0, 0xFFFFFFFF):
                programmer = board.create_jtag_programmer(chain)
                # LSC_READ_STATUS only. Nothing here enables configuration or
                # erases SRAM, so a running design survives being asked.
                ev.status = programmer._read_status()
    except Exception as failure:
        say(f"  jtag: {failure}")
    finally:
        try:
            board.close()
        except Exception:
            pass
    if ev.idcode is not None:
        say(f"  jtag: IDCODE {ev.idcode:08x}"
            + (f", status {ev.status:08x} (DONE "
               f"{'set' if ev.status & STATUS_DONE else 'CLEAR'})"
               if ev.status is not None else ", status unreadable"))


def ask_identity(ev):
    """IDCODE and USERCODE, and what the build that was loaded expected.

    Only when there IS an expectation: without one there is nothing to compare
    against, and JTAG_START drives the pins the design's second UART sits on --
    so an unconditional read would disturb a healthy board to learn nothing.
    """
    from soc_usercode import read_identity

    try:
        ev.idcode, ev.usercode = read_identity()
    except Exception as failure:
        say(f"  jtag identity: {failure}")
        return
    say(f"  jtag: IDCODE "
        f"{'unread' if ev.idcode is None else f'{ev.idcode:08x}'}, USERCODE "
        f"{'unread' if ev.usercode is None else f'{ev.usercode:08x}'}")


def gather(*, node=None, jtag=True, expect="shell", expected=None):
    """Every probe, cheapest first, stopping as soon as the verdict is decided.

    `expected` is the build record beside the bitstream that was loaded
    (`gateware/usercode_map.py`). Given one, the part's identity is read FIRST:
    a stale bitstream answers a prompt as readily as the right one, so asking
    the console first would report `ok` before the question was put.
    """
    import usb_ids

    if expect not in EXPECT:
        raise ValueError(f"expect must be one of {EXPECT}, not {expect!r}")
    ev = Evidence(expect=expect, expected=expected)
    if expected and jtag:
        ask_identity(ev)
        if from_identity(ev) is not None:
            return ev
    ev.apollo = usb_present(APOLLO_PID)
    ev.console_usb = usb_present(usb_ids.product_id(CONSOLE))
    ev.console_node = node or usb_ids.find_tty(CONSOLE)
    say(f"  usb: 1d50:{APOLLO_PID:04x} "
        f"{'present' if ev.apollo else 'ABSENT'} (Apollo, CONTROL), "
        f"1d50:{usb_ids.product_id(CONSOLE):04x} "
        f"{'present' if ev.console_usb else 'ABSENT'} (console, AUX)")

    if expect == "console" and ev.console_usb:
        return ev
    if expect == "shell" and (ev.console_usb or node):
        ask_console(ev, node)
        if (ev.reply and ev.reply.strip()) or ev.thieves:
            return ev
    if not ev.console_usb and ev.apollo and jtag:
        if expect == "shell":
            ask_jtag_console(ev)
        if not (ev.jtag_console and ev.jtag_console.strip()):
            ask_jtag(ev)
    return ev


def confirm(*, node=None, jtag=True, expect="shell", expected=None):
    """Gather, diagnose, and say so. Returns the Verdict."""
    say(f"confirming a design is running, expecting a {expect} (#360):")
    if expected is None:
        say("  no build record to compare against: WHICH bitstream is running "
            "is not decided here")
    verdict = diagnose(gather(node=node, jtag=jtag, expect=expect,
                              expected=expected))
    if verdict.ok:
        say(f"CONFIRMED: {verdict.headline}")
    else:
        say(f"NOT RUNNING [{verdict.name}]: {verdict.headline}")
    for line in verdict.lines:
        say(line)
    return verdict


def precheck():
    """The gate BEFORE configuring: is the board there to configure at all?

    `soc_run.py` already refused to configure an absent board rather than
    proceeding, and that behaviour is the counterexample to #360. Keeping it
    here means every path gets it, not just that one.
    """
    import usb_ids

    ev = Evidence(apollo=usb_present(APOLLO_PID),
                  console_usb=usb_present(usb_ids.product_id(CONSOLE)))
    if ev.apollo:
        return None
    if ev.console_usb:
        return Verdict("control-unplugged", "the board is here, its CONTROL cable is not", [
            "  1d50:6180 (console, AUX) is on the bus but 1d50:615c (Apollo) is",
            "  not. Apollo lives on CONTROL and nothing can configure without it.",
        ])
    return Verdict("board-absent", "no part of this board is on USB", [
        "  neither 1d50:615c (Apollo, CONTROL) nor 1d50:6180 (console, AUX)",
        "  Refusing to configure rather than reporting a success nothing backs.",
    ])


def wait_for_console(limit_s=CONSOLE_ENUMERATE_S):
    """After a configure: the console back on the bus, or the elapsed on record.

    Configuring drops `1d50:6180` and the host re-enumerates it. Confirming
    inside that window scores `wrong-port` on a board that is fine -- #419, and
    the first `run` job the arbiter ever submitted failed exactly that way.
    Presence only, no tty open: `tio_user.py` holds that node and the boot banner
    is flushed to whoever reads first.
    """
    import usb_ids

    started = time.perf_counter()
    while True:
        if usb_present(usb_ids.product_id(CONSOLE)):
            elapsed = time.perf_counter() - started
            say(f"  console re-enumerated after {elapsed:.2f} s")
            return elapsed
        if time.perf_counter() - started >= limit_s:
            say(f"  console did not re-enumerate within {limit_s:.1f} s "
                f"(elapsed {time.perf_counter() - started:.1f} s); confirming "
                f"anyway, which will name what is actually there")
            return None
        subprocess.run(["udevadm", "settle"], capture_output=True)
        time.sleep(ENUMERATE_TICK_S)


def configure_and_confirm(bitstream, *, tries=3, node=None, expect="shell"):
    """Configure the FPGA and prove a design is running. 0 on success.

    THE ONE PATH. Per-script liveness gates were growing after each script got
    bitten (`hyperram_pin_axis.py`), each with its own idea of what "alive" means.

    Retries are for the retryable verdicts only -- JTAG contention and an unbound
    tty. A blank FPGA is retried too: it is the failure that a second configure
    fixes, and the one that cost the four sessions in #360.
    """
    blocked = precheck()
    if blocked is not None:
        say(f"REFUSING to configure [{blocked.name}]: {blocked.headline}")
        for line in blocked.lines:
            say(line)
        return 1

    # WHAT WAS LOADED, from beside the bitstream itself rather than from an
    # argument. This is the party doing the loading, so it is the party that can
    # be held to the claim -- and the record travels with the .bit through the
    # bitcache, so a restored bitstream is still attributable.
    import usercode_map

    expected = usercode_map.read_build_record(Path(bitstream).parent)
    if expected is None:
        say(f"  no {usercode_map.RECORD_NAME} beside {Path(bitstream).name}: "
            f"the identity of what is being loaded is not checkable")

    from subprocess_timeout_from_history import run_bounded

    for attempt in range(1, tries + 1):
        result = run_bounded(
            [sys.executable, str(apollo_cli()), "configure", str(bitstream)],
            family="run:apollo-configure", cwd=ROOT, merge_stderr=True)
        if result is None or result.returncode != 0:
            tail = ((result.stdout or "") if result else "").strip().splitlines()
            say(f"  configure attempt {attempt}/{tries} FAILED: "
                + (tail[-1][:160] if tail else "killed at its time limit"))
            continue
        if expect != "design":
            wait_for_console()
        verdict = confirm(node=node, jtag=True, expect=expect, expected=expected)
        if verdict.ok:
            say(f"configured {Path(bitstream).name} and the design answers"
                + (f" (attempt {attempt})" if attempt > 1 else ""))
            return 0
        if verdict.name in ("silent-firmware", "stolen-port", "wrong-port",
                            "board-absent", "control-unplugged",
                            # A second configure of the same file loads the same
                            # bitstream onto the same die. Retrying restates it.
                            "wrong-part", "stale-bitstream", "declared-clock"):
            # Retrying cannot change any of these. Stop and say which it is
            # rather than spending two more configures on the same answer.
            return 1
        say(f"  configure attempt {attempt}/{tries} returned 0 but the board "
            f"did not come up [{verdict.name}]")
    say(f"configure failed {tries} times for {bitstream}")
    return 1


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true",
                        help="print the evidence and verdict as one object")
    parser.add_argument("--before", action="store_true",
                        help="the pre-configure gate only: is the board there")
    parser.add_argument("--node", help="a tty to use instead of the USB console")
    parser.add_argument("--no-jtag", action="store_true",
                        help="skip the JTAG probe; USB and the console only")
    parser.add_argument("--expect-build", type=Path,
                        help="a build directory whose usercode.json the part "
                             "must match; without it, WHICH bitstream is "
                             "running is not decided")
    parser.add_argument("--expect", choices=EXPECT, default="shell",
                        help="how far confirmation can go for this bitstream: "
                             "prompt the shell, require the console to "
                             "enumerate, or stop at the part's DONE bit")
    args = parser.parse_args()

    global QUIET
    QUIET = args.json
    # Everything that is not the object goes to stderr while probing: the
    # imported helpers print their own progress to stdout, and one mixed stream
    # is not parseable by the caller that asked for JSON.
    stdout = sys.stdout
    if QUIET:
        sys.stdout = sys.stderr

    if args.before:
        import usb_ids

        blocked = precheck()
        verdict = blocked or Verdict("ok", "Apollo is on the bus", [])
        ev = Evidence(apollo=usb_present(APOLLO_PID),
                      console_usb=usb_present(usb_ids.product_id(CONSOLE)))
    else:
        import usercode_map

        expected = (usercode_map.read_build_record(args.expect_build)
                    if args.expect_build else None)
        if args.expect_build and expected is None:
            say(f"no {usercode_map.RECORD_NAME} in {args.expect_build}")
        started = time.perf_counter()
        ev = gather(node=args.node, jtag=not args.no_jtag,
                    expect=args.expect, expected=expected)
        verdict = diagnose(ev)
        say(f"  ({time.perf_counter() - started:.1f} s of evidence)")

    if args.json:
        sys.stdout = stdout
        print(json.dumps({"verdict": verdict.name, "headline": verdict.headline,
                          "retry": verdict.retry, "evidence": ev.as_json()},
                         indent=2))
    else:
        say(f"{'CONFIRMED' if verdict.ok else 'NOT RUNNING'} [{verdict.name}]: "
            f"{verdict.headline}")
        for line in verdict.lines:
            say(line)
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    sys.exit(main())
