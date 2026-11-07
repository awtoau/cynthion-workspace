#!/usr/bin/env python3
#
# The target-port test table, its runner, the seeded fuzzer and the board backend.
# SPDX-License-Identifier: BSD-3-Clause

"""
One table of cases, two backends (docs/target-test-harness.md).

    ./scripts/soc/usb_target_cases.py --list                  # the table, with support per backend
    ./scripts/soc/usb_target_cases.py --board --port target_a # same table on the board shell
    ./scripts/soc/usb_target_cases.py --board --fuzz 20 --seed 7

- Case = name, group, needs (bus level), steps (op, args, expected result).
- Sim backend: `usb_target_sim.py` (LUNA models). Board backend: here, `usb <port> ...` lines via soc_shell.
- Results are driver-level (what `usb <port> in/out/ctrl` prints), so both backends compare alike.
- Never run on the board by the agent that wrote it: written against the firmware text, unverified.
"""

import argparse
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "probes"))

CASE_STALL_S = 60.0        # no case may run longer: a hang is a failure, not a wait (house rule)
CTRL_BUF = 64              # firmware control/IN buffer: IN data beyond it is discarded
TX_FIFO = 128              # host TX FIFO depth: an OUT packet over it is refused (EV_TX_OVERFLOW)

# Levels of bus state a case needs; the backend moves between them with the fewest steps.
NONE, DEFAULT, ADDRESSED, CONFIGURED = 0, 1, 2, 3
DEV_ADDR = 1

ANY = frozenset({"ack", "nak", "stall", "timeout", "crc", "overflow", "dropped"})


# --------------------------------------------------------------------------- model

@dataclass
class Result:
    kind: str                       # ack nak stall timeout crc overflow dropped | hang noreset error
    data: bytes = b""
    info: dict = field(default_factory=dict)
    text: str = ""


@dataclass
class Exp:
    """Spec-legal outcome (`kinds`) plus optional `pin`: what the LUNA model does exactly."""
    kinds: frozenset = frozenset({"ack"})
    length: object = None           # int | callable(profile)
    max_length: object = None       # int: data may be shorter, never longer
    data: object = None             # bytes | callable(profile)
    check: object = None            # callable(result, profile) -> str | None
    info: dict = field(default_factory=dict)
    pin: "Exp" = None


def E(*kinds, **kw):
    return Exp(kinds=frozenset(kinds or {"ack"}), **kw)


@dataclass
class Step:
    op: str
    args: dict
    exp: Exp = None


def S(op, exp=None, **args):
    return Step(op, args, exp)


@dataclass
class Case:
    name: str
    group: str
    steps: list
    needs: int = CONFIGURED
    after: object = None            # level left behind; None = unchanged, NONE = unknown
    profiles: tuple = ("model", "model_fs", "aoa_phone", "generic")
    sim: object = None              # None = supported, else the reason it is not
    known: object = None            # reason: an engine bug this case is expected to expose (XFAIL)
    board: object = None
    about: str = ""


@dataclass
class Profile:
    name: str
    exact: bool                     # True: the LUNA model, so `pin` expectations apply
    speed: str = None
    vid: int = None
    pid: int = None
    bulk_mps: int = 512
    dev: bytes = None
    cfg: bytes = None
    strings: dict = field(default_factory=dict)     # index -> text
    bulk_ep: int = None
    int_ep: int = None
    aoa: bool = False


def _model_profile(name, full_speed):
    from usb_host import target_device as td
    from usb_host.target_device import descriptors
    mps = 64 if full_speed else 512
    d = descriptors(mps)
    return Profile(name, True, "FS" if full_speed else "HS", td.VID, td.PID, mps,
                   bytes(d.get_descriptor_bytes(1)), bytes(d.get_descriptor_bytes(2)),
                   {1: td.MANUFACTURER, 2: td.PRODUCT, 3: td.SERIAL}, 1, 2)


def get_profile(name):
    if name == "model":
        return _model_profile(name, False)
    if name == "model_fs":
        return _model_profile(name, True)
    if name == "aoa_phone":      # 18d1:4ee2 as in gateware/probes/usb_host/aoa_phone.py
        return Profile(name, True, "HS", 0x18D1, 0x4EE2, aoa=True,
                       strings={1: "Google", 2: "Pixel", 3: "0001"})
    if name == "generic":        # any device: structure only
        return Profile(name, False)
    raise SystemExit(f"unknown profile {name}")


# ------------------------------------------------------------------ expectations

def verify(exp, res, prof):
    """None when `res` meets `exp` on this profile, else why not."""
    for e in ([exp] + ([exp.pin] if exp.pin and prof.exact else [])):
        if res.kind not in e.kinds:
            return f"kind {res.kind} not in {sorted(e.kinds)} ({res.text.strip()[:80]})"
        want = e.length(prof) if callable(e.length) else e.length
        if want is not None and len(res.data) != want:
            return f"length {len(res.data)} != {want}"
        if e.max_length is not None and len(res.data) > e.max_length:
            return f"length {len(res.data)} > {e.max_length}"
        want = e.data(prof) if callable(e.data) else e.data
        if want is not None and res.data != want:
            return f"data {res.data.hex()} != {want.hex()}"
        for key, value in e.info.items():
            got = res.info.get(key)
            value = value(prof) if callable(value) else value
            if value is not None and got != value:
                return f"info {key}={got} != {value}"
        if e.check:
            why = e.check(res, prof)
            if why:
                return why
    return None


def nak_is_refusal(step, res, prof):
    """`generic` only (Dan, #572): a control request that may STALL may also NAK until the
    limit; the case ends there as a pass and the bus is reset before the next one."""
    return (prof.name == "generic" and step.op == "ctrl" and step.exp is not None
            and "stall" in step.exp.kinds and res.kind == "nak")


def sp(bm, req, val=0, idx=0, length=0):
    return bytes([bm, req, val & 255, val >> 8, idx & 255, idx >> 8, length & 255, length >> 8])


def utf16(text):
    return bytes([2 + 2 * len(text), 3]) + text.encode("utf-16-le")


def trunc(blob, wlength):
    """What a control IN returns: the descriptor cut to wLength and to the shell buffer."""
    return blob[:min(wlength, CTRL_BUF)]


# ----------------------------------------------------------------------- the table

def table():
    cases = []

    def add(*a, **k):
        cases.append(Case(*a, **k))

    GET_DESC = 6
    WL0 = "LUNA control EP gives no status stage to an IN request with wLength 0 (IN and OUT both time out)"
    BOUNDARY = (0, 1, 7, 8, 63, 64, 65, 512)

    # ---- control: descriptors x wLength --------------------------------------------
    for wl in BOUNDARY:
        add(f"ctrl_get_device_desc_wl{wl}", "ctrl_desc",
            [S("ctrl", E(length=lambda p, wl=wl: trunc(p.dev, wl).__len__() if p.dev else None,
                         data=lambda p, wl=wl: trunc(p.dev, wl) if p.dev else None,
                         check=lambda r, p, wl=wl: None if len(r.data) < 2 or r.data[1] == 1
                         else "not a device descriptor"),
               bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=wl)],
            needs=ADDRESSED, sim=WL0 if wl == 0 else None,
            about="GET_DESCRIPTOR(device); wLength cuts the data stage")
        add(f"ctrl_get_config_desc_wl{wl}", "ctrl_desc",
            [S("ctrl", E(length=lambda p, wl=wl: len(trunc(p.cfg, wl)) if p.cfg else None,
                         data=lambda p, wl=wl: trunc(p.cfg, wl) if p.cfg else None,
                         check=lambda r, p, wl=wl: None if len(r.data) < 2 or r.data[1] == 2
                         else "not a configuration descriptor"),
               bm=0x80, req=GET_DESC, val=0x0200, idx=0, len=wl)],
            needs=ADDRESSED, sim=WL0 if wl == 0 else None)
        # product string is 64 bytes: wLength 64 ends on a full packet, 65/512 need the ZLP
        add(f"ctrl_get_product_string_wl{wl}", "ctrl_string",
            [S("ctrl", E(length=lambda p, wl=wl: min(wl, 64, 2 + 2 * len(p.strings[2]))
                         if p.strings.get(2) and p.exact else None,
                         data=lambda p, wl=wl: trunc(utf16(p.strings[2]), wl)
                         if p.strings.get(2) and p.exact else None),
               bm=0x80, req=GET_DESC, val=0x0302, idx=0x0409, len=wl)],
            needs=ADDRESSED, profiles=("model", "model_fs"), sim=WL0 if wl == 0 else None)

    # ---- control: ID strings -------------------------------------------------------
    for label, index in (("manufacturer", 1), ("product", 2), ("serial", 3)):
        def check(r, p, index=index):
            if p.strings.get(index) is None:
                return None if len(r.data) < 2 or r.data[1] == 3 else "not a string descriptor"
            return None if r.data == utf16(p.strings[index]) else f"string {r.data!r}"
        add(f"ctrl_string_{label}", "ctrl_string",
            [S("ctrl", E(check=check), bm=0x80, req=GET_DESC, val=0x0300 | index, idx=0x0409, len=64)],
            needs=ADDRESSED)
    add("ctrl_string_language_ids", "ctrl_string",
        [S("ctrl", E(data=bytes([4, 3, 0x09, 0x04]),
                     check=lambda r, p: None if r.data[:2] == bytes([4, 3]) else "no lang id list"),
           bm=0x80, req=GET_DESC, val=0x0300, idx=0, len=64)],
        needs=ADDRESSED, about="index 0 returns the LANGID list; wIndex 0")
    add("ctrl_string_unsupported_langid", "ctrl_string",
        [S("ctrl", E("ack", "stall"), bm=0x80, req=GET_DESC, val=0x0302, idx=0x0407, len=64)],
        needs=ADDRESSED, about="German langid: device may answer or stall")
    add("ctrl_string_missing_index", "ctrl_string",
        [S("ctrl", E("stall", pin=E("stall")), bm=0x80, req=GET_DESC, val=0x03F0, idx=0x0409, len=64)],
        needs=ADDRESSED, about="no such string: STALL")
    add("ctrl_desc_missing_type", "ctrl_string",
        [S("ctrl", E("stall"), bm=0x80, req=GET_DESC, val=0x0900, idx=0, len=64)],
        needs=ADDRESSED, about="no such descriptor type")
    add("ctrl_device_qualifier", "ctrl_string",
        [S("ctrl", E("ack", "stall"), bm=0x80, req=GET_DESC, val=0x0600, idx=0, len=10)],
        needs=ADDRESSED, about="HS devices answer; FS-only devices STALL")

    # ---- control: every standard request -------------------------------------------
    # #624: generic takes STALL or ACK (<= wLength); the LUNA model (pin) stays STALL
    either_in = E("stall", "ack", max_length=2, pin=E("stall"))
    std = [
        ("get_status_device", 0x80, 0, 0, 0, 2, E("ack", length=2)),
        ("get_status_interface", 0x81, 0, 0, 0, 2, E("ack", "stall")),
        ("get_status_endpoint_bulk", 0x82, 0, 0, 0x81, 2, E("ack", "stall")),
        ("get_status_endpoint_bad", 0x82, 0, 0, 0x8F, 2, E("stall", "ack", "timeout")),
        ("get_configuration", 0x80, 8, 0, 0, 1,
         E("ack", length=1, data=bytes([1]), pin=E("ack", data=bytes([1])))),
        ("get_interface", 0x81, 10, 0, 0, 1, E("ack", "stall")),
        ("set_interface_0", 0x01, 11, 0, 0, 0, E("ack", "stall")),
        ("clear_feature_ep_halt", 0x02, 1, 0, 0x81, 0, E("ack", "stall")),
        ("clear_feature_remote_wakeup", 0x00, 1, 1, 0, 0, E("ack", "stall")),
        ("synch_frame", 0x82, 12, 0, 0x81, 2, E("ack", "stall")),
        ("set_descriptor", 0x00, 7, 0x0100, 0, 0, E("ack", "stall")),
        ("unknown_standard_request", 0x80, 0x7F, 0, 0, 2, E("stall")),
        ("vendor_request_in", 0xC0, 1, 0, 0, 2, either_in),
        ("vendor_request_out", 0x40, 1, 0, 0, 0, E("stall")),
        ("class_request_in", 0xA1, 1, 0, 0, 2, either_in),
        ("reserved_request_type", 0xE0, 1, 0, 0, 2, E("stall", "ack", "timeout")),
    ]
    for name, bm, req, val, idx, wl, exp in std:
        add(f"ctrl_{name}", "ctrl_std",
            [S("ctrl", exp, bm=bm, req=req, val=val, idx=idx, len=wl)],
            profiles=("model", "model_fs", "generic"),
            sim="LUNA EP0 stops answering every later request after CLEAR_FEATURE(DEVICE_REMOTE_WAKEUP)"
            if name == "clear_feature_remote_wakeup" else None)
    add("ctrl_set_then_clear_feature_ep_halt", "ctrl_std",
        [S("ctrl", E("ack", "stall"), bm=0x02, req=3, val=0, idx=0x81, len=0),
         S("ctrl", E("ack", "stall"), bm=0x02, req=1, val=0, idx=0x81, len=0),
         S("ctrl", E(length=18), bm=0x80, req=6, val=0x0100, idx=0, len=18)],
        profiles=("model", "model_fs", "generic"), about="halt then un-halt EP1 IN; state restored")
    # A stalled control request must not wedge EP0.
    add("ctrl_stall_then_good", "ctrl_std",
        [S("ctrl", E("stall"), bm=0x80, req=0x7F, val=0, idx=0, len=2),
         S("ctrl", E(check=lambda r, p: None if len(r.data) == 18 else "no descriptor"),
           bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=18)],
        about="STALL clears on the next SETUP")
    # #572: the Pixel NAKs unhandled requests, then stops answering SETUP until a bus reset.
    nak_forever = [
        S("ctrl", E("nak"), bm=0xC0, req=0x5A, val=0, idx=0, len=2),
        S("ctrl", E("nak"), bm=0x40, req=0x5A, val=0, idx=0, len=0),
        S("ctrl", E("nak"), bm=0xC0, req=0x5A, val=0, idx=0, len=64),
        S("ctrl", E("nak"), bm=0x40, req=0x5A, val=0, idx=0, len=4, out=b"\x01\x02\x03\x04")]
    add("ctrl_nak_forever_then_good", "ctrl_std",
        nak_forever + [
            S("ctrl", E("stall"), bm=0x80, req=0x7F, val=0, idx=0, len=2),
            S("ctrl", E(length=18), bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=18)],
        profiles=("model", "model_fs"), board="vendor request 0x5A is the model's NAK-forever request",
        about="four requests abandoned after NAKs (data IN, status IN, data IN, data OUT); "
              "the next SETUPs are ACKed: the host side keeps no state from an abandoned transfer")

    # ---- SET_ADDRESS / SET_CONFIGURATION -------------------------------------------
    GD = dict(bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=18)
    add("set_address_chain_5_127_1", "address",
        [S("ctrl", E(), bm=0, req=5, val=5, idx=0, len=0), S("setaddr", None, addr=5),
         S("ctrl", E(length=18), **GD),
         S("setaddr", None, addr=DEV_ADDR),
         S("ctrl", E("timeout"), **GD),
         S("setaddr", None, addr=5),
         S("ctrl", E(), bm=0, req=5, val=127, idx=0, len=0), S("setaddr", None, addr=127),
         S("ctrl", E(length=18), **GD),
         S("ctrl", E(), bm=0, req=5, val=DEV_ADDR, idx=0, len=0), S("setaddr", None, addr=DEV_ADDR),
         S("ctrl", E(length=18), **GD)],
        needs=ADDRESSED, about="SET_ADDRESS from the address state; the old address stops answering")
    add("set_address_0_returns_to_default", "address",
        [S("ctrl", E(), bm=0, req=5, val=0, idx=0, len=0), S("setaddr", None, addr=0),
         S("ctrl", E(length=18), **GD),
         S("ctrl", E(), bm=0, req=5, val=DEV_ADDR, idx=0, len=0), S("setaddr", None, addr=DEV_ADDR),
         S("ctrl", E(length=18), **GD)],
        needs=ADDRESSED, about="SET_ADDRESS(0) = default state, then address again")
    add("transfer_to_wrong_address", "address",
        [S("setaddr", None, addr=33),
         S("ctrl", E("timeout"), bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=18),
         S("setaddr", None, addr=DEV_ADDR),
         S("ctrl", E(length=18), bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=18)],
        needs=ADDRESSED, about="nobody answers: TIMEOUT after the driver's retries; next one is fine")
    add("set_configuration_roundtrip", "address",
        [S("ctrl", E(data=bytes([1])), bm=0x80, req=8, val=0, idx=0, len=1),
         S("ctrl", E(), bm=0, req=9, val=0, idx=0, len=0),
         S("ctrl", E(data=bytes([0])), bm=0x80, req=8, val=0, idx=0, len=1),
         S("ctrl", E(), bm=0, req=9, val=1, idx=0, len=0),
         S("ctrl", E(data=bytes([1])), bm=0x80, req=8, val=0, idx=0, len=1)],
        needs=CONFIGURED, profiles=("model", "model_fs"))
    add("set_configuration_invalid", "address",
        [S("ctrl", E("stall", "ack"), bm=0, req=9, val=7, idx=0, len=0),
         S("ctrl", E(length=18), bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=18)],
        needs=CONFIGURED, about="configuration 7 does not exist")

    # ---- control SETUP stage alone, and a data stage the device never gets --------
    add("setup_stage_only", "ctrl_std",
        [S("setup", E(), data=sp(0x80, GET_DESC, 0x0100, 0, 18)),
         S("ctrl", E(length=18), bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=18)],
        about="SETUP then a fresh SETUP abandons the first transfer")

    # ---- bulk OUT/IN: sizes --------------------------------------------------------
    def echo(n, prof_ok):
        data = bytes((i * 7 + 3) & 255 for i in range(n))
        return [S("bulk_out", E(), ep=1, data=data),
                S("bulk_in", E(length=n, data=data), ep=1, n=n)]

    for n in (1, 2, 7, 8, 31, 63, 64, 65, 100, 127, 128, 129, 255, 256, 511, 512):
        add(f"bulk_echo_{n}", "bulk", echo(n, True), profiles=("model", "model_fs"),
            board="shell `in`/`out` carry at most 64 bytes" if n > 64 else None,
            about="OUT n bytes, IN returns the same packet")
    add("bulk_zlp_out", "bulk",
        [S("bulk_out", E(), ep=1, data=b""),
         S("bulk_out", E(), ep=1, data=b"\x5a" * 4),
         S("bulk_in", E(data=b"\x5a" * 4, length=4), ep=1, n=64)],
        profiles=("model", "model_fs"), board="shell `out` needs at least one byte (no ZLP form)",
        about="a ZLP OUT is ACKed (the echo of it is not asserted)")
    add("bulk_in_short_packet", "bulk",
        [S("bulk_out", E(), ep=1, data=b"\x11\x22\x33"),
         S("bulk_in", E(length=3, data=b"\x11\x22\x33"), ep=1, n=64)],
        profiles=("model", "model_fs"), about="a short packet ends the IN; length from RXLEN")
    add("bulk_in_nak_no_data", "bulk",
        [S("bulk_in", E("nak"), ep=1, n=8)],
        profiles=("model", "model_fs"), about="nothing to echo: NAK until the driver gives up")
    add("bulk_wrong_endpoint", "bulk",
        [S("bulk_out", E("timeout", "stall", "nak"), ep=9, data=b"\x00"),
         S("ctrl", E(length=18), bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=18)],
        profiles=("model", "model_fs"), about="endpoint the device does not have")

    # ---- interrupt IN --------------------------------------------------------------
    def counter_ok(r, p):
        d = list(r.data)
        return None if len(d) == 8 and d == [(d[0] + i) & 255 for i in range(8)] else f"counter {d}"

    add("interrupt_in_8", "bulk_int",
        [S("bulk_in", E(length=8, check=counter_ok), ep=2, n=8)],
        profiles=("model", "model_fs"), about="interrupt IN is a bulk-style IN token on ep 2")
    add("interrupt_in_toggle_sequence", "bulk_int",
        [S("bulk_in", E(length=8), ep=2, n=8),
         S("bulk_in", E(length=8), ep=2, n=8),
         S("bulk_in", E(length=8), ep=2, n=8)],
        profiles=("model", "model_fs"), about="DATA0/DATA1 alternate; every packet accepted")
    add("interrupt_in_short_buffer", "bulk_int",
        [S("bulk_in", E(length=4), ep=2, n=4),
         S("bulk_in", E(length=8), ep=2, n=8)],
        profiles=("model", "model_fs"), about="host buffer smaller than the packet: tail discarded, bus unharmed")

    # ---- data toggle ---------------------------------------------------------------
    add("toggle_duplicate_out_discarded", "bulk_int",
        [S("ctrl", E(), bm=0x02, req=1, val=0, idx=0x01, len=0), S("ctrl", E(), bm=0x02, req=1, val=0, idx=0x81, len=0),
         S("bulk_out", E(), ep=1, data=b"\xaa", toggle=0),
         S("bulk_out", E(), ep=1, data=b"\xbb", toggle=0),
         S("bulk_in", E(data=b"\xaa", length=1), ep=1, n=64)],
        profiles=("model", "model_fs"), after=NONE,
        board="no toggle override in the shell verbs",
        about="a repeated toggle is ACKed and dropped (spec 8.5.2); only the first byte echoes")
    add("toggle_wrong_first_out", "bulk_int",
        [S("ctrl", E(), bm=0x02, req=1, val=0, idx=0x01, len=0), S("ctrl", E(), bm=0x02, req=1, val=0, idx=0x81, len=0),
         S("bulk_out", E("ack", "nak", "timeout"), ep=1, data=b"\xcc", toggle=1),
         S("bulk_in", E("ack", "nak"), ep=1, n=64)],
        profiles=("model", "model_fs"), after=NONE, board="no toggle override in the shell verbs",
        about="a fresh endpoint expects DATA0; DATA1 first is ACKed (data accepted or ignored)")
    add("toggle_in_mismatch_discarded", "bulk_int",
        [S("ctrl", E(), bm=0x02, req=1, val=0, idx=0x01, len=0), S("ctrl", E(), bm=0x02, req=1, val=0, idx=0x81, len=0),
         S("bulk_out", E(), ep=1, data=b"\xaa", toggle=0),
         S("bulk_in", E("nak"), ep=1, n=64, toggle=1),
         S("bulk_out", E(), ep=1, data=b"\xbb", toggle=1),
         S("bulk_in", E(data=b"\xbb", length=1), ep=1, n=64, toggle=1)],
        profiles=("model", "model_fs"), after=NONE, board="no toggle override in the shell verbs",
        about="IN DATA0 while DATA1 is due (a resend after a lost ACK): ACKed and dropped (8.6.4, #627)")
    add("toggle_alternates_across_sizes", "bulk_int",
        [S(op, E(length=1, data=bytes([i])) if op == "bulk_in" else E(), ep=1,
           **({"n": 64} if op == "bulk_in" else {"data": bytes([i])}))
         for i in range(6) for op in ("bulk_out", "bulk_in")],
        profiles=("model", "model_fs"), about="six OUT/IN pairs in order; every toggle accepted")
    add("toggle_restarts_on_clear_halt", "bulk_int",
        [S("bulk_out", E(), ep=1, data=b"\x01"),
         S("bulk_in", E(length=1), ep=1, n=64),
         S("ctrl", E(), bm=0x02, req=1, val=0, idx=0x01, len=0),
         S("ctrl", E(), bm=0x02, req=1, val=0, idx=0x81, len=0),
         S("bulk_out", E(), ep=1, data=b"\x02"),
         S("bulk_in", E(length=1, data=b"\x02"), ep=1, n=64)],
        profiles=("model", "model_fs"),
        about="CLEAR_FEATURE(ENDPOINT_HALT) restarts the toggle on both sides (driver and device)")

    # Before mps_plus_1: on a wedged engine the next case's own recovery overruns the stall guard.
    add("bulk_out_oversize_then_bus_reset", "bulk_int",
        [S("bulk_out", Exp(kinds=ANY | {"hang"}), ep=1, data=b"\xa5" * 513),
         S("reset", E()),
         S("enumerate", E(length=4), addr=DEV_ADDR),
         S("ctrl", E(length=18), **GD)],
        profiles=("model",), board="shell `out` takes at most 64 bytes", after=ADDRESSED,
        about="REGRESSION (#572): a bus reset alone returns the engine to service, whatever the OUT did")
    add("bulk_out_mps_plus_1", "bulk_int",
        [S("bulk_out", E("dropped"), ep=1, data=b"\xa5" * 513),
         S("recover", E(length=18))],
        profiles=("model",), board="shell `out` takes at most 64 bytes", after=NONE,
        about="REGRESSION (#572): one byte over the SIE's 512 B buffer is refused (dropped), never hangs")

    # ---- handshakes ----------------------------------------------------------------
    add("handshake_nak", "handshake",
        [S("bulk_in", E("nak"), ep=1, n=8)], profiles=("model", "model_fs"))
    add("handshake_stall", "handshake",
        [S("ctrl", E("stall"), bm=0x80, req=0x7F, val=0, idx=0, len=2)])
    add("handshake_timeout_no_device_at_address", "handshake",
        [S("setaddr", None, addr=77),
         S("bulk_out", E("timeout"), ep=1, data=b"\x00"),
         S("setaddr", None, addr=DEV_ADDR),
         S("ctrl", E(length=18), bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=18)],
        profiles=("model", "model_fs", "generic"))
    inject = "needs the wire fault injector (sim) or a misbehaving device"
    add("handshake_timeout_response_dropped", "handshake",
        [S("fault", None, mode="drop"),
         S("bulk_out", E("timeout"), ep=1, data=b"\x01\x02"),
         S("fault", None, mode="none"),
         S("ctrl", E(length=18), bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=18)],
        profiles=("model", "model_fs"), board=inject)
    add("handshake_crc_error_in_data", "handshake",
        [S("bulk_out", E(), ep=1, data=bytes(range(16))),
         S("fault", None, mode="crc"),
         S("bulk_in", E("crc"), ep=1, n=64),
         S("fault", None, mode="none"),
         S("ctrl", E(length=18), bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=18)],
        profiles=("model", "model_fs"), board=inject, after=NONE,
        about="one data byte of the device packet flipped: CRC_ERROR after 3 tries; bus recovers")
    add("handshake_crc_then_clean_in", "handshake",
        # Two INs first: earlier cases leave packets in the echo (a dropped-ACK OUT is delivered).
        [S("bulk_in", Exp(kinds=ANY), ep=1, n=64), S("bulk_in", Exp(kinds=ANY), ep=1, n=64),
         S("bulk_out", E(), ep=1, data=bytes(range(16))),
         S("fault", None, mode="crc"),
         S("bulk_in", E("crc"), ep=1, n=64),
         S("fault", None, mode="none"),
         S("bulk_in", E(data=bytes(range(16)), length=16), ep=1, n=64)],
        profiles=("model", "model_fs"), board=inject, after=NONE,
        about="the CRC-bad copies must not reach the next good IN (#627): RX is empty after any response")
    add("handshake_pid_corrupt", "handshake",
        [S("fault", None, mode="pid"),
         S("ctrl", E("timeout", "crc"), bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=18),
         S("fault", None, mode="none"),
         S("ctrl", E(length=18), bm=0x80, req=GET_DESC, val=0x0100, idx=0, len=18)],
        profiles=("model", "model_fs"), board=inject, after=NONE)
    add("handshake_babble", "handshake", [], sim="no babble injector: would need extra bytes past EOP",
        board=inject, about="RX_OVERFLOW (resp 6) path")
    add("handshake_ping_unsupported", "handshake", [],
        sim="engine has no PING token (sie.py: TODO PING); OUT reports ACK",
        board="same", about="HS OUT flow control relies on NAK + retry instead")

    # ---- resets and events (order matters: each reset costs ~12 s of simulation) ---
    add("reset_mid_transfer", "events",
        [S("reset_during_in", E(), ep=1),
         S("link", E(info={"connected": True})),
         S("enumerate", E(length=4), addr=DEV_ADDR)],
        needs=ADDRESSED, after=ADDRESSED, profiles=("model", "model_fs"),
        board="shell is single-threaded: cannot reset during a transfer",
        about="bus_reset written while an IN is in flight; engine returns to idle, device re-enumerates")
    add("reset_returns_device_to_default", "events",
        [S("reset", E()),
         S("setaddr", None, addr=DEV_ADDR),
         S("ctrl", E("timeout", "stall"), **GD),
         S("setaddr", None, addr=0),
         S("ctrl", E(length=18), **GD),
         S("enumerate", E(length=4, check=lambda r, p: None
            if p.vid is None or (r.data[0] | r.data[1] << 8, r.data[2] | r.data[3] << 8) == (p.vid, p.pid)
            else f"id {r.data.hex()}"), addr=DEV_ADDR)],
        needs=ADDRESSED, after=ADDRESSED, about="bus reset: address 0 again, then re-enumerate")
    spd = {"connected": True, "speed": lambda p: p.speed}
    add("reset_x3_keeps_speed_and_drives_phy_reset_mode", "events_regress",
        [S("reset", E()), S("link", E(info=spd)),
         S("clear_phy_writes", None), S("reset", E()), S("link", E(info=spd)),
         S("phy_writes", E(check=lambda r, p: None if any(v & 0x18 == 0x10 for v in r.info["writes"])
                           else f"no RAW_DRIVE write, saw {r.info['writes']}")),
         S("reset", E()), S("link", E(info=spd)),
         S("enumerate", E(length=4), addr=DEV_ADDR)],
        needs=NONE, after=ADDRESSED, profiles=("model", "model_fs"),
        board="PHY function-control writes are not visible from the shell (use usb trace reset)",
        about="REGRESSION: a later bus reset left the PHY in FS (sie.py); speed holds at every reset "
              "and the PHY gets op_mode RAW_DRIVE")
    add("role_cycle_reenumerate", "events_misc",
        [S("role", E(), name="regs"), S("role", E(), name="host"),
         S("link", E(info=spd)),
         S("enumerate", E(length=4), addr=DEV_ADDR)],
        needs=NONE, after=ADDRESSED, profiles=("model",),
        about="leave host (SOFs stop) and re-enter: PHY init + fresh reset")
    add("attach_detach_sim", "events_detach",
        [S("detach", None),
         S("link", E(info={"connected": False})),
         S("ctrl", E("timeout", "hang"), **GD),
         S("attach", None),
         S("reset", E()),
         S("link", E(info=spd)),
         S("enumerate", E(length=4), addr=DEV_ADDR)],
        needs=ADDRESSED, after=ADDRESSED, profiles=("model_fs",),
        board="cannot unplug from the shell; see vbus_cycle_reenumerate",
        about="detach: LINK.connected clears, a transfer gives up (timeout or no EV_DONE); attach + reset re-enumerates")
    add("vbus_cycle_reenumerate", "events_detach",
        [S("vbus", None, state="off"), S("link", E(info={"connected": False})),
         S("vbus", None, state="on"), S("await_connect", E()),
         S("reset", E()), S("enumerate", E(length=4), addr=DEV_ADDR)],
        needs=ADDRESSED, after=ADDRESSED, profiles=("model_fs",),
        about="power cycle the target; sim approximates VBUS loss as detach/attach (no VBUS model); "
              "board: `usb <port> power off|on`, run with --allow-vbus")
    add("suspend_resume", "events_misc", [],
        sim="engine has no suspend/resume control: SOFs only stop by leaving host role",
        board="same", about="SOF gap > 3 ms suspends; resume signalling absent")
    add("speed_high", "events_misc",
        [S("link", E(info={"speed": "HS", "connected": True}))],
        needs=ADDRESSED, profiles=("model",))
    add("speed_full", "events",
        [S("link", E(info={"speed": "FS", "connected": True}))],
        needs=ADDRESSED, profiles=("model_fs",))
    add("speed_low", "events_misc", [], sim="LUNA device models are HS/FS only",
        board="needs an LS device; LINK.speed has an LS code, SIE LS support unverified",
        about="LS: 8 B packets, no chirp")
    add("iso_in_out", "events_misc", [],
        sim="engine EP.type has no ISO code (0 SETUP 1 IN 2 OUT) and the SIE does not support iso",
        board="same", about="isochronous transfers")
    add("sof_gap", "events_misc", [], sim="no control over SOF generation",
        board="same", about="SOF/uSOF gap injection")

    # ---- AOA -----------------------------------------------------------------------
    aoa = ("aoa_phone",)
    add("aoa_get_protocol", "aoa",
        [S("ctrl", E(data=bytes([2, 0]), pin=E(data=bytes([2, 0]))), bm=0xC0, req=51, val=0, idx=0, len=2)],
        needs=ADDRESSED, profiles=aoa)
    add("aoa_get_protocol_wlength_1", "aoa",
        [S("ctrl", E("ack", "stall", length=None), bm=0xC0, req=51, val=0, idx=0, len=1)],
        needs=ADDRESSED, profiles=aoa, about="wLength 1 for a 2-byte answer: device may truncate")
    for n in (0, 1, 7, 8, 63, 64):
        add(f"aoa_send_string_len{n}", "aoa",
            [S("ctrl", E(), bm=0x40, req=52, val=0, idx=0, len=n, out=bytes((i + 0x41) & 0x7F for i in range(n)))],
            needs=ADDRESSED, profiles=aoa,
            board="shell `ctrl` takes 56 OUT bytes" if n > 56 else None,
            about="control OUT data stage across the 8/64 boundaries")
    add("aoa_send_string_bad_index", "aoa",
        [S("ctrl", E("ack", "stall"), bm=0x40, req=52, val=0, idx=9, len=4, out=b"abc\0")],
        needs=ADDRESSED, profiles=aoa)
    add("aoa_sequence_51_52x6_53_reenumerate_bulk", "aoa_seq",
        [S("aoa_full", E(info={"accessory": True}))],
        needs=ADDRESSED, after=CONFIGURED, profiles=aoa,
        about="51, 52 x6 (DJI strings), 53, resets until 18d1:2d00/2d01, configure, bulk echo")
    # ---- our own device engine only (group own_bulk_x; the AUX-side stand-ins are usb_own_device.BulkModel) ----
    own = ("model",)
    halt = lambda ep: S("ctrl", E(), bm=0x02, req=3, val=0, idx=ep, len=0)       # noqa: E731  SET_FEATURE(HALT)
    unhalt = lambda ep: S("ctrl", E(), bm=0x02, req=1, val=0, idx=ep, len=0)     # noqa: E731  CLEAR_FEATURE(HALT)
    add("own_zlp_echo", "own_bulk_x",
        [S("bulk_out", E(), ep=1, data=b""), S("bulk_in", E(length=0), ep=1, n=64)],
        profiles=own, about="a ZLP OUT reaches the AUX side as a ZLP and comes back as a ZLP IN")
    add("own_short_packets_echo", "own_bulk_x",
        [step for n in (1, 2, 3, 100) for step in
         (S("bulk_out", E(), ep=1, data=bytes(range(n))), S("bulk_in", E(length=n, data=bytes(range(n))), ep=1, n=128))],
        profiles=own, about="short packets keep their boundaries; toggles alternate across them")
    add("own_halt_out_in", "own_bulk_x",
        [halt(0x01), S("bulk_out", E("stall"), ep=1, data=b"\x01"),
         halt(0x81), S("bulk_in", E("stall"), ep=1, n=8),
         unhalt(0x01), unhalt(0x81),
         S("bulk_out", E(), ep=1, data=b"\x07\x08"), S("bulk_in", E(length=2, data=b"\x07\x08"), ep=1, n=8)],
        profiles=own, about="halt STALLs both legs, clear restarts the toggles and traffic resumes")
    add("own_in_pattern_3_packets", "own_bulk_x",
        [S("bulk_in", E(length=512, data=_pattern(0, 512)), ep=3, n=512),
         S("bulk_in", E(length=512, data=_pattern(512, 512)), ep=3, n=512),
         S("bulk_in", E(length=37, data=_pattern(1024, 37)), ep=3, n=512),
         S("bulk_in", E(length=512, data=_pattern(0, 512)), ep=3, n=512)],
        profiles=own, about="2*512+37 byte transfer from the AUX side: split at 512, next transfer starts clean")
    add("own_out_backpressure_echo", "own_bulk_x",
        [S("bulk_out", E(), ep=4, data=bytes((i * 3 + k) & 255 for i in range(100))) for k in range(6)]
        + [S("bulk_in", E(length=100, data=bytes((i * 3 + k) & 255 for i in range(100))), ep=4, n=128)
           for k in range(6)],
        profiles=own, about="a throttled AUX consumer: the device NAKs / NYETs, the host retries, order and data hold")
    return cases


def _pattern(start, n):
    return bytes(((start + i) * 5 + 1) & 255 for i in range(n))


# --------------------------------------------------------------------------- fuzz

def fuzz_cases(seed, count, prof):
    """Reproducible random cases. Case i of seed s is the same list every run."""
    rng = random.Random(seed)
    cases = []
    sizes = (0, 1, 7, 8, 63, 64, 65, 512, 65535)
    for i in range(count):
        steps, picks = [], rng.randint(1, 4)
        for _ in range(picks):
            kind = rng.choice(("desc", "string", "echo", "int", "garbage", "wrong_addr") * 2 + ("reset",))
            if kind == "desc":
                wl = rng.choice(sizes + (rng.randint(0, 600),))
                wl = wl or 1 if prof.exact else wl      # LUNA: no status stage for IN wLength 0
                typ, blob = rng.choice(((1, prof.dev), (2, prof.cfg)))
                steps.append(S("ctrl", E(data=(lambda p, b=blob, w=wl: trunc(b, w)) if blob and prof.exact else None,
                                         check=lambda r, p, t=typ: None if len(r.data) < 2 or r.data[1] == t
                                         else "wrong descriptor type"),
                               bm=0x80, req=6, val=typ << 8, idx=0, len=wl))
            elif kind == "string":
                index = rng.randint(0, 6)
                known = index in (0, 1, 2, 3)
                steps.append(S("ctrl", E(*(["ack"] if known else ["stall"])),
                               bm=0x80, req=6, val=0x0300 | index, idx=0x0409 if index else 0,
                               len=rng.choice(sizes[:-1]) or (1 if prof.exact else 0)))
            elif kind == "echo" and prof.bulk_ep:
                n = rng.choice([0, 1, 7, 8, 63, 64, 65, 100, 128, rng.randint(1, 128)])
                n = min(n, prof.bulk_mps, TX_FIFO)
                data = bytes(rng.randrange(256) for _ in range(n))
                steps.append(S("bulk_out", E(), ep=1, data=data))
                if n:
                    steps.append(S("bulk_in", E(data=data, length=n), ep=1, n=max(n, 64)))
            elif kind == "int" and prof.int_ep:
                steps.append(S("bulk_in", E(length=8), ep=2, n=8))
            elif kind == "garbage":
                req = rng.choice([r for r in range(256) if r not in (3, 1, 5, 7, 9, 11)])
                steps.append(S("ctrl", Exp(kinds=ANY), bm=rng.choice((0x80, 0xC0, 0xA1, 0x40, 0x21, 0x82)),
                               req=req, val=rng.randrange(65536), idx=rng.randrange(65536),
                               len=rng.choice(sizes[:-1]) or (1 if prof.exact else 0)))
            elif kind == "wrong_addr":
                steps += [S("setaddr", None, addr=rng.choice([a for a in range(1, 128) if a != DEV_ADDR])),
                          S("bulk_out", E("timeout"), ep=1, data=b"\x00"),
                          S("setaddr", None, addr=DEV_ADDR)]
            elif kind == "reset":
                steps += [S("reset", E())] + [S("enumerate", E(length=4), addr=DEV_ADDR),
                                                                  S("ctrl", E(), bm=0, req=9, val=1, idx=0, len=0)]
        # Oracle of last resort: after anything, the device still answers a descriptor.
        steps.append(S("recover", E(length=18)))
        cases.append(Case(f"fuzz_seed{seed}_{i}", "fuzz", steps, needs=CONFIGURED, after=None,
                          profiles=(prof.name,), about=f"replay: --seed {seed} --only fuzz_seed{seed}_{i}"))
    return cases


# ------------------------------------------------------------------------ runner

async def run_case(backend, case, prof, emit):
    """Returns (status, detail); status in PASS FAIL UNSUPPORTED."""
    why = getattr(case, backend.name)
    if why:
        return "UNSUPPORTED", why
    if prof.name not in case.profiles:
        return "SKIP", f"profile {prof.name} not in {case.profiles}"
    started = backend.case_clock()
    try:
        await backend.ensure(case.needs)
        for n, step in enumerate(case.steps):
            if backend.case_stalled(started):
                return "FAIL", f"stall guard: case over its limit, step {n} {step.op}"
            res = await backend.op(step.op, **step.args)
            if step.exp is None:
                continue
            if nak_is_refusal(step, res, prof):
                backend.invalidate()        # EP0 stays wedged until a bus reset (#572)
                return "PASS", f"step {n}: NAK until limit, accepted as a refusal on {prof.name}; bus reset next"
            problem = verify(step.exp, res, prof)
            if problem:
                trail = ""
                if case.group == "fuzz":
                    trail = " | steps " + "; ".join(f"{x.op} {_brief(x.args)}" for x in case.steps[:n + 1])
                if case.known:
                    backend.invalidate()
                    return "XFAIL", f"{case.known} [{problem[:100]}]"
                return "FAIL", f"step {n} {step.op} {_brief(step.args)}: {problem}{trail}"
    except Exception as error:      # a backend bug or a wedged board must name itself
        backend.invalidate()
        return "FAIL", f"exception {type(error).__name__}: {error}"
    backend.after(case.after)
    if case.known:
        return "FAIL", f"XPASS: expected to fail ({case.known}); remove `known`"
    return "PASS", ""


def _brief(args):
    return {k: (v.hex() if isinstance(v, (bytes, bytearray)) and len(v) > 12 else v)
            for k, v in args.items()}


# ------------------------------------------------------------------ board backend

REPLY_BUDGET_S = 3.0        # longest shell verb: BULK_US 100 ms x 3 tries, 8 s AOA re-attach is run in steps
RESET_BUDGET_S = 1.0        # `reset`: 63 ms engine limit + 10 ms recovery; 1 s is the prompt wait bound
ATTACH_ATTEMPTS = 8         # aoa re-attach loop, as the driver (ATTACH_US 8 s / 100 ms gap, capped)
CONNECT_POLLS = 40          # await_connect: x 0.25 s; a phone re-attaches in < 3 s after VBUS returns


def parse_reply(text):
    """Shell reply -> Result. Patterns are the firmware's own strings (shell/usb_host.rs, Error::fmt)."""
    t = text.replace("\r", "")
    m = re.search(r"^(\d+) bytes\s*\n((?:[0-9a-f]{2} )*)", t, re.M)
    if m:
        cut = re.search(r"^truncated: packet (\d+) bytes", t, re.M)
        return Result("ack", bytes.fromhex(m.group(2).replace(" ", "")),
                      {"packet": int(cut.group(1))} if cut else {}, t)
    if re.search(r"^(ack|reset done|queued|addr \d+)", t, re.M):
        return Result("ack", b"", {}, t)
    m = re.search(r"^([0-9a-f]{4}):([0-9a-f]{4})", t, re.M)
    if m:
        data = bytes.fromhex(m.group(1))[::-1] + bytes.fromhex(m.group(2))[::-1]
        return Result("ack", data, {}, t)
    if re.search(r"^STALL", t, re.M):
        return Result("stall", b"", {}, t)
    m = re.search(r"^handshake (\d)", t, re.M)
    if m:
        return Result({"4": "timeout", "5": "crc", "6": "overflow"}.get(m.group(1), "error"), b"", {}, t)
    m = re.search(r"^timeout: (\w[\w ]*?) limit", t, re.M)
    if m:
        op = m.group(1)
        kind = "nak" if op.startswith(("bulk", "control", "setup")) else \
               "noreset" if op.startswith(("bus reset", "role")) else "hang"
        return Result(kind, b"", {}, t)
    if re.search(r"^transfer dropped", t, re.M):
        return Result("dropped", b"", {}, t)
    return Result("error", b"", {}, t)


class Backend:
    """Shared bus-level bookkeeping. Subclasses implement `op` and set `self.level`."""

    level = NONE

    def case_clock(self):
        return time.monotonic()

    def case_stalled(self, started):
        return self.case_clock() - started > CASE_STALL_S

    async def reset_bus(self, then_enumerate):
        """Bus reset; if the engine or bus is wedged, escalate once to a role cycle (regs, host)."""
        r = await self.op("reset")
        if r.kind != "ack" or (then_enumerate and not await self.try_enumerate()):
            for name in ("regs", "host"):
                await self.op("role", name=name)
            r = await self.op("reset")
            if r.kind != "ack":
                raise RuntimeError(f"reset failed after role cycle: {r.kind} {r.text.strip()}")
            if then_enumerate:
                await self.enumerate_bus()
        self.level = ADDRESSED if then_enumerate else DEFAULT

    async def try_enumerate(self):
        r = await self.op("enumerate", addr=DEV_ADDR)
        if r.kind == "ack":
            self.level = ADDRESSED
        else:
            self.level = NONE
        return r.kind == "ack"

    async def enumerate_bus(self):
        if not await self.try_enumerate():
            raise RuntimeError("enumerate failed")

    def invalidate(self):
        self.level = NONE

    def after(self, level):
        if level is not None:
            self.level = level

    async def ensure(self, need):
        """Move to bus level `need` with the fewest steps (a reset is the only way down)."""
        if need == NONE or self.level == need:
            return
        if self.level == NONE or need < self.level or need == DEFAULT:
            await self.reset_bus(need >= ADDRESSED)
        if need >= ADDRESSED and self.level < ADDRESSED:
            await self.enumerate_bus()
        if need >= CONFIGURED and self.level < CONFIGURED:
            r = await self.op("ctrl", bm=0, req=9, val=1, idx=0, len=0)
            if r.kind != "ack":
                raise RuntimeError(f"set_configuration failed: {r.kind}")
            self.level = CONFIGURED


class PowerNotReady(Exception):
    """The target port is unpowered and the run was not allowed to power it."""


class BoardBackend(Backend):
    """The same ops as `usb <port> ...` lines. Written from the firmware source; not run."""

    name = "board"

    def __init__(self, link, port, profile, emit, allow_vbus=False):
        self.link, self.port, self.prof, self.emit = link, port, profile, emit
        self.allow_vbus = allow_vbus
        self.level = NONE
        self.speed_names = ("HS", "FS", "LS", "?")

    # -- shell plumbing: one line out, one reply back, bounded by the prompt wait
    def say(self, line, budget=REPLY_BUDGET_S):
        self.link.write(line.encode() + b"\r")
        self.link.settle(0.05)
        reply = self.link.read_until_prompt(budget).decode("ascii", "replace")
        # drop the echoed command and prompt redraws
        body = "\n".join(l for l in reply.replace("\r", "").split("\n")
                         if line not in l and not l.strip() in (">", ""))
        return body

    def usb(self, rest, budget=REPLY_BUDGET_S):
        return parse_reply(self.say(f"usb {self.port} {rest}", budget))

    async def op(self, _op, **a):
        return getattr(self, f"do_{_op}")(**a)

    def do_reset(self):
        r = self.usb("reset", RESET_BUDGET_S)
        self.level = DEFAULT if r.kind == "ack" else NONE
        return r

    def do_enumerate(self, addr):
        r = self.usb(f"enum {addr}")
        self.level = ADDRESSED if r.kind == "ack" else NONE
        return r

    def do_setaddr(self, addr):
        return self.usb(f"addr {addr}")

    def do_ctrl(self, bm, req, val, idx, len, out=b""):
        tail = (" " + out.hex(" ")) if out else ""
        return self.usb(f"ctrl {sp(bm, req, val, idx, len).hex(' ')}{tail}")

    def do_setup(self, data):
        return self.usb(f"setup {data.hex(' ')}")

    def do_bulk_in(self, ep, n, toggle=None):
        return self.usb(f"in {ep} {min(n, 64)}")

    def do_bulk_out(self, ep, data, toggle=None):
        if not data:
            return Result("error", text="shell `out` has no ZLP form")
        return self.usb(f"out {ep} {data.hex(' ')}")

    def do_link(self):
        t = self.say(f"usb {self.port} role")
        m = re.search(r"link ([0-9a-f]{2}) ls (\d) (\w+) up (\d)", t)
        if not m:
            return Result("error", text=t)
        link = int(m.group(1), 16)
        return Result("ack", info={"connected": bool(link & 0x10), "speed": m.group(3),
                                   "reset_done": bool(link & 0x80), "ls": int(m.group(2))}, text=t)

    def do_role(self, name):
        r = self.usb(f"role {name}")
        self.level = NONE
        return Result("ack" if "role" in r.text else "error", text=r.text)

    def do_vbus(self, state):
        if not self.allow_vbus:
            return Result("error", text="--allow-vbus not given")
        self.level = NONE
        t = self.say(f"usb {self.port} power {'off' if state == 'off' else 'on'}")
        return Result("ack" if t.strip().startswith(state) else "error", text=t)

    def power_state(self):
        """(on, reply): the register's view from `usb <port> power`; on is None if unparsable."""
        t = self.say(f"usb {self.port} power")
        m = re.match(r"(on|off)\s+c\d a\d t\d", t.strip())
        return (m.group(1) == "on" if m else None), t

    def ensure_power(self, allow):
        """The port is powered before `role host`. Returns the prior state (True = was on).

        Unpowered and `allow` unset fails loudly with the command that fixes it (#625).
        """
        on, text = self.power_state()
        if on is None:
            raise PowerNotReady(f"`usb {self.port} power` gave {text!r}")
        if not on:
            if not allow:
                raise PowerNotReady(f"{self.port} is not powered; pass --power or run "
                                    f"`usb {self.port} power on`")
            t = self.say(f"usb {self.port} power on")
            if not t.strip().startswith("on "):
                raise PowerNotReady(f"`usb {self.port} power on` refused: {t.strip()!r}")
        return on

    def restore_power(self, was_on):
        """Leave the port as `ensure_power` found it."""
        on, _ = self.power_state()
        if on and not was_on:
            self.say(f"usb {self.port} power off")
        elif on is False and was_on:
            self.say(f"usb {self.port} power on")

    def do_await_connect(self):
        for _ in range(CONNECT_POLLS):
            r = self.do_link()
            if r.info.get("connected"):
                return Result("ack", info=r.info)
            self.link.settle(0.25)
            self.link.read_available()
        return Result("noreset", text="device did not attach after VBUS returned")

    def do_aoa_full(self):
        r = self.usb("aoa", 12.0)
        m = re.search(r"accessory ([0-9a-f]{4}):([0-9a-f]{4})", r.text)
        return Result("ack" if m else r.kind, info={"accessory": bool(m)}, text=r.text)

    def do_recover(self):
        r = self.do_reset()
        if r.kind == "ack":
            r = self.do_enumerate(DEV_ADDR)
        if r.kind != "ack":
            return r
        self.usb("ctrl 00 09 01 00 00 00 00 00")
        self.level = CONFIGURED
        return self.do_ctrl(0x80, 6, 0x0100, 0, 18)


# ----------------------------------------------------------------------- selftest

class FakeLink:
    """Canned shell: records the lines sent, answers from `replies` keyed by verb."""

    def __init__(self, replies):
        self.replies, self.sent, self._last = replies, [], ""

    def write(self, data):
        self._last = data.decode().strip()
        self.sent.append(self._last)

    def settle(self, seconds):
        pass

    def read_until_prompt(self, budget):
        verb = self._last.split(" ")[2] if self._last.startswith("usb ") else self._last
        return (self._last + "\r\n" + self.replies.get(verb, "") + "\r\n> ").encode()

    def read_available(self):
        return b""


def selftest():
    """The reply parser and the op -> shell-line mapping, from the firmware's own strings."""
    import asyncio
    bad = []

    def expect(what, got, want):
        if got != want:
            bad.append(f"{what}: {got!r} != {want!r}")

    for text, kind, data in (
            ("ack", "ack", b""), ("18 bytes\n12 01 00 02 ", "ack", bytes([0x12, 1, 0, 2])),
            ("STALL", "stall", b""), ("handshake 4", "timeout", b""), ("handshake 5", "crc", b""),
            ("timeout: bulk in limit 100000 us elapsed 100100 us", "nak", b""),
            ("timeout: bus reset limit 63000 us elapsed 63001 us", "noreset", b""),
            ("transfer dropped (events 48)", "dropped", b""),
            ("18d1:4ee2", "ack", bytes([0xD1, 0x18, 0xE2, 0x4E])), ("reset done", "ack", b"")):
        r = parse_reply(text)
        expect(f"parse {text!r}", (r.kind, r.data), (kind, data))
    # #627: `in` with a buffer under the packet says so (the toggle has advanced, the tail is gone).
    r = parse_reply("2 bytes\n01 02 \ntruncated: packet 512 bytes")
    expect("parse truncated in", (r.kind, r.data, r.info.get("packet")), ("ack", b"\x01\x02", 512))
    link = FakeLink({"reset": "reset done", "enum": "18d1:4ee2", "ctrl": "ack", "in": "2 bytes\n01 02 ",
                     "role": "role host  switching 0  timeout 0  link d1 ls 1 HS up 1 rx 0 tx 0"})
    b = BoardBackend(link, "target_a", get_profile("generic"), print)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(b.ensure(CONFIGURED))
    expect("ensure lines", link.sent, ["usb target_a reset", "usb target_a enum 1",
                                       "usb target_a ctrl 00 09 01 00 00 00 00 00"])
    r = loop.run_until_complete(b.op("bulk_in", ep=1, n=512))
    expect("bulk_in", (link.sent[-1], r.data), ("usb target_a in 1 64", b"\x01\x02"))
    r = loop.run_until_complete(b.op("link"))
    expect("link", r.info, {"connected": True, "speed": "HS", "reset_done": True, "ls": 1})
    unhandled = Case("t", "t", [S("ctrl", E("stall"), bm=0xC0, req=1, val=0, idx=0, len=2)], needs=NONE)
    for name, reply, want in (("generic", "timeout: control data in limit 500000 us elapsed 500100 us",
                               ("PASS", NONE)), ("model", "timeout: control data in limit 1 us elapsed 2 us",
                                                 ("FAIL", None))):
        link = FakeLink({"ctrl": reply})
        b = BoardBackend(link, "target_a", get_profile(name), print)
        b.level = CONFIGURED
        status, _ = loop.run_until_complete(run_case(b, unhandled, get_profile(name), print))
        expect(f"NAK refusal on {name}", (status, b.level if status == "PASS" else None), want)
    # #625: power gate before `role host`.
    for name, reply, allow, want in (
            ("on", "on  c1 a0 t0", False, (True, [])),
            ("off, not allowed", "off  c0 a0 t0", False, "POWER"),
            ("garbled", "?", True, "POWER")):
        link = FakeLink({"power": reply})
        b = BoardBackend(link, "target_a", get_profile("generic"), print)
        try:
            got = (b.ensure_power(allow), link.sent[1:])
        except PowerNotReady:
            got = "POWER"
        expect(f"ensure_power {name}", got, want)
    link = FakeLink({"power": "off  c0 a0 t0"})
    b = BoardBackend(link, "target_a", get_profile("generic"), print)
    # canned reply cannot turn on, so the refusal is what is reported
    try:
        b.ensure_power(True)
        expect("ensure_power refused", "no exception", "PowerNotReady")
    except PowerNotReady:
        expect("ensure_power on line", link.sent, ["usb target_a power", "usb target_a power on"])
    link = FakeLink({"power": "on  c1 a0 t0"})
    b = BoardBackend(link, "target_a", get_profile("generic"), print)
    b.restore_power(False)
    expect("restore turns off what it turned on", link.sent[-1], "usb target_a power off")
    # #624: generic accepts STALL or ACK (<= wLength) for class/vendor IN; the model stays STALL.
    gen, mod = get_profile("generic"), Profile("m", True)
    either = E("stall", "ack", max_length=2, pin=E("stall"))
    for what, res, prof, want in (
            ("stall", Result("stall"), gen, True), ("ack 2", Result("ack", b"\1\2"), gen, True),
            ("ack 0", Result("ack"), gen, True), ("timeout", Result("timeout"), gen, False),
            ("nak", Result("nak"), gen, False), ("ack 3 (over wLength)", Result("ack", b"\1\2\3"), gen, False),
            ("model ack", Result("ack", b"\1\2"), mod, False)):
        expect(f"either stall/ack: {what}", verify(either, res, prof) is None, want)
    cases = {c.name: c for c in table()}
    for name in ("ctrl_class_request_in", "ctrl_vendor_request_in"):
        exp = cases[name].steps[0].exp
        expect(f"{name} generic accepts ack", verify(exp, Result("ack", b"\1\2"), gen) is None, True)
        expect(f"{name} model still stall", verify(exp, Result("ack", b"\1\2"), mod) is None, False)
    emit = print
    for line in bad:
        emit("FAIL " + line)
    emit("selftest: " + ("FAILED" if bad else "ok"))
    return 1 if bad else 0


# --------------------------------------------------------------------- CLI (board)

def select(cases, prof, only, groups):
    out = []
    for c in cases:
        if only and not any(o in c.name for o in only):
            continue
        if groups and c.group not in groups:
            continue
        out.append(c)
    return out


def main():
    import asyncio
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="print the table and exit")
    parser.add_argument("--selftest", action="store_true",
                        help="board backend against canned shell replies (no hardware)")
    parser.add_argument("--board", action="store_true", help="run on the board shell")
    parser.add_argument("--port", default="target_a", choices=("target_a", "target_c"))
    parser.add_argument("--profile", default="generic",
                        choices=("model", "model_fs", "aoa_phone", "generic"),
                        help="what is plugged into the target port")
    parser.add_argument("--fuzz", type=int, default=0, help="N seeded random cases after the table")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--only", nargs="*", default=[], help="substrings of case names")
    parser.add_argument("--group", nargs="*", default=[])
    parser.add_argument("--allow-vbus", action="store_true",
                        help="permit `vbus off` (opens every switch) for the power-cycle case")
    parser.add_argument("--power", action="store_true",
                        help="power the port (`usb <port> power on`) if it is off, and restore it after")
    parser.add_argument("--console", help="tty node instead of the USB console")
    parser.add_argument("--via-arbiter", action="store_true",
                        help="one board.py job per command; the tty stays the arbiter's")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    prof = get_profile(args.profile)
    cases = table()

    if args.list:
        for c in cases:
            sim = "sim:" + ("yes" if not c.sim else "NO ")
            brd = "board:" + ("yes" if not c.board else "NO ")
            print(f"{c.group:10} {c.name:50} {sim} {brd} need={c.needs} {c.sim or c.board or ''}")
        print(f"{len(cases)} cases")
        return 0
    if not args.board:
        parser.error("pass --board (sim: scripts/soc/usb_target_sim.py)")

    from soc_shell import ArbiterLink, Link
    from devlog import emit
    cases = select(cases + fuzz_cases(args.seed, args.fuzz, prof), prof, args.only, args.group)
    link = ArbiterLink() if args.via_arbiter else Link.open(args.console)
    emit(f"console: {link.how}  port {args.port}  profile {prof.name}  fuzz seed {args.seed}")
    link.write(b"\r")
    link.settle(0.05)
    link.read_until_prompt(2.0)
    backend = BoardBackend(link, args.port, prof, emit, args.allow_vbus)
    try:
        was_on = backend.ensure_power(args.power)
    except PowerNotReady as e:
        emit(f"POWER: {e}")
        link.close()
        return 2
    try:
        backend.say(f"usb {args.port} role host", 4.0)

        counts = {"PASS": 0, "FAIL": 0, "XFAIL": 0, "UNSUPPORTED": 0, "SKIP": 0}
        loop = asyncio.new_event_loop()
        for case in cases:
            status, detail = loop.run_until_complete(run_case(backend, case, prof, emit))
            counts[status] += 1
            if status == "FAIL":        # as the sim: bus reset before the next case, so one wedge
                backend.invalidate()    # (the Pixel's EP0 after NAKed requests, #572) does not cascade
            if status != "SKIP":
                emit(f"  {status:11} {case.name}  {detail}")
            if status == "FAIL" and case.group == "fuzz":
                emit(f"      replay: --seed {args.seed} --only {case.name}")
    finally:
        backend.restore_power(was_on)
    link.close()
    emit(f"{counts}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
