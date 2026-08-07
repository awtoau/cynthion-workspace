#!/usr/bin/env python3
"""extract-hardware.py — teach the topology GUI what the schematic knows.

Reads a KiCad netlist (exported by `kicad-cli`, so the pin-to-net mapping is
KiCad's own and not a guess) and writes two kinds of fact into
`gui/assets/hardware/cynthion.json`:

  * per node — part number, description and, for connectors, the pin list with
    the net on each pin;
  * per connection — which interface it is, the nets that make it up, the
    voltage domain, a one-line description for the hover chip, and which end
    drives.

Usage:

    gui/tools/extract-hardware.py --kicad repos/cynthion-hardware
    gui/tools/extract-hardware.py --netlist tmp/cynthion-netlist.xml --check

`--kicad DIR` runs `kicad-cli sch export netlist` for you and caches the XML.
`--check` prints what it would write and touches nothing.

Why a netlist and not the .kicad_sch files: two components are connected when
KiCad says they share a net.  Reading symbol properties out of the schematic
s-expressions — what the first version of this script did — cannot see a net at
all, so every connection came out empty.

The one thing the netlist does not hand you is that almost nothing on this board
is wired chip-to-chip.  The ULPI bus between the FPGA and the TARGET PHY runs
through two 0 Ω resistor arrays, so `IC1` and `U9` share exactly one net (the
reset line) and share nothing that looks like a bus.  So series passives are
treated as short circuits and their nets merged, except where one side is a
supply — a pull-up is not a connection, it is the clue that tells you the
signalling voltage, and it is used as that instead.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_BOARD = REPO / "gui/assets/hardware/cynthion.json"
DEFAULT_NETLIST = REPO / "tmp/cynthion-netlist.xml"

# ── Net classification ────────────────────────────────────────────────────────

# A supply or return.  These touch every component on the board, so they are
# never evidence of a connection between two of them; they are only ever the
# answer to "what voltage is this".
_POWER_RE = re.compile(r"""^(
      GND\w* | VSS\w* | AGND | GNDPWR
    | [+-]\d+V\d*         # +3V3, +1V1, -5V
    | [+-]\d+V            # +5V
    | VBUS\w* | VCC\w* | VDD\w* | VCONN\w* | VREF\w*
)$""", re.X)

_VOLTS = {
    "+5V": "5 V", "VBUS": "5 V (VBUS)", "+3V3": "3.3 V", "+2V5": "2.5 V",
    "+1V8": "1.8 V", "+1V15": "1.15 V", "+1V1": "1.1 V",
}

# Leaf-name fragment → interface.  Ordered: the first hit wins, so put the
# specific names above the ones that also appear inside them.
_INTERFACE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"JTAG|\bT(CK|DI|DO|MS)\b"), "JTAG"),
    (re.compile(r"SWD|SWCLK|SWDIO"), "SWD"),
    (re.compile(r"ULPI|^DATA[0-7]$|^(STP|NXT|DIR)$"), "ULPI"),
    (re.compile(r"HYPER|^RAM\b|RWDS|^DQ[0-7]$"), "HyperBus"),
    (re.compile(r"I2C|^SCL$|^SDA$|^MON$"), "I²C"),
    (re.compile(r"SPI|MOSI|MISO|POCI|PICO|^IO[0-3]$|FLASH"), "SPI"),
    (re.compile(r"UART|^(TX|RX|TXD|RXD)$"), "UART"),
    (re.compile(r"^D[+-]$|_D[+-]$|USB|^(DP|DM)$"), "USB2"),
    (re.compile(r"^CC[12]$|^SBU[12]$|VCONN"), "USB-C CC"),
    (re.compile(r"PROGRAM|DONE|INIT|CFG|CONFIG"), "config"),
    (re.compile(r"^LED|LED\d"), "LED"),
    (re.compile(r"BTN|BUTTON"), "button"),
    (re.compile(r"RESET|RST"), "reset"),
    (re.compile(r"^CLK$|CLOCK|REFCLK"), "clock"),
    (re.compile(r"ADV|SENSE|GPIO|PMOD|BANK"), "GPIO"),
]

# Refs whose pins may be a short circuit for the purpose of "what is connected
# to what": links, series termination, ferrites, common-mode chokes. Capacitors
# and diodes are shunts, not links, and are deliberately absent.
_BRIDGE_RE = re.compile(r"^(R|RN|L|FB|FL)\d")

# Above this, a resistor is not a link. Series termination on this board is 33 Ω
# and current-sense shunts are 0.02 Ω; everything else is 330 Ω and up, and
# those are pull-ups and dividers.
#
# Bridging every resistor was the first rule, and it shorted CONTROL_D+ to
# CONTROL_D- through the two-resistor network that drives CONTROL_RESET_DETECT.
# The connector and the MCU then appeared to share one net whose name flipped
# between D+ and D- from run to run.
_LINK_OHMS = 100.0

_VALUE_RE = re.compile(r"^([\d.]+)\s*([munkKMR]?)", re.A)
_SI = {"": 1.0, "R": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "m": 1e-3, "u": 1e-6}


def ohms(value: str) -> float | None:
    """`0`, `33`, `2.2k`, `0.02±1%` → resistance, or None if unreadable."""
    m = _VALUE_RE.match(value.strip())
    if not m:
        return None
    try:
        return float(m.group(1)) * _SI.get(m.group(2), 1.0)
    except ValueError:
        return None

# `R1.1` / `R1.2` in a resistor array's pinfunction: element, then terminal.
_ELEMENT_RE = re.compile(r"^([A-Za-z]+\d+)\.\d")


def leaf(net: str) -> str:
    """`/FPGA Banks 6 and 7/RAM.~{CS}` → `RAM.~CS`.

    The sheet path is noise in a hover chip. The overbar becomes a `~` prefix
    rather than being dropped: dropping it collapsed `RAM.CK` and `RAM.~{CK}`
    into one name, and a differential clock pair that reads as a duplicate is
    worse than no name at all.
    """
    # Only hierarchical names carry a sheet path. A global name may contain a
    # slash of its own -- `Net-(U6-PA28/~{RST})` -- and splitting it produced
    # the nonsense net name `RST)`.
    name = net.rsplit("/", 1)[-1] if net.startswith("/") else net
    return name.replace("~{", "~").replace("}", "")


def is_power(net: str) -> bool:
    return bool(_POWER_RE.match(leaf(net).split(".")[-1])) or bool(
        _POWER_RE.match(leaf(net)))


def pretty_volts(net: str) -> str:
    return _VOLTS.get(leaf(net), leaf(net))


# ── Netlist ───────────────────────────────────────────────────────────────────

class Netlist:
    def __init__(self, path: Path):
        root = ET.parse(path).getroot()

        self.components: dict[str, dict] = {}
        for c in root.findall("components/comp"):
            fields = {f.get("name"): (f.text or "")
                      for f in (c.find("fields") if c.find("fields") is not None else ())}
            self.components[c.get("ref")] = {
                "value": c.findtext("value") or "",
                "description": c.findtext("description") or "",
                "footprint": c.findtext("footprint") or "",
                "datasheet": c.findtext("datasheet") or "",
                "fields": fields,
            }

        # net name → [(ref, pin, pinfunction, pintype)]
        self.nets: dict[str, list[tuple[str, str, str, str]]] = {}
        self.pins: dict[str, list[dict]] = defaultdict(list)
        for net in root.findall("nets/net"):
            name = net.get("name")
            members = []
            for n in net:
                entry = (n.get("ref"), n.get("pin"),
                         n.get("pinfunction") or "", n.get("pintype") or "")
                members.append(entry)
                self.pins[n.get("ref")].append({
                    "pin": n.get("pin"),
                    "function": n.get("pinfunction") or "",
                    "type": n.get("pintype") or "",
                    "net": name,
                })
            self.nets[name] = members

        self._parent: dict[str, str] = {n: n for n in self.nets}
        self.pullups: dict[str, set[str]] = defaultdict(set)
        self._bridge()

    # ── union-find over nets joined by series passives ────────────────────────
    def find(self, net: str) -> str:
        while self._parent[net] != net:
            self._parent[net] = self._parent[self._parent[net]]
            net = self._parent[net]
        return net

    def _union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra

    def _bridge(self) -> None:
        """Merge the nets on either side of every series passive.

        A passive with one end on a supply is a pull-up/pull-down/decoupler: the
        nets are recorded as related for voltage inference but never merged, or
        every signal on the board would end up in one net called +3V3.
        """
        for ref, pins in sorted(self.pins.items()):
            if not _BRIDGE_RE.match(ref):
                continue
            # An inductor or choke is always a link. A resistor is one only if
            # it is small enough to be one rather than a pull-up — but a big
            # resistor is still read for the rail it ties to, which is the whole
            # point of noticing it.
            is_link = True
            if ref.startswith(("R", "RN")):
                r = ohms(self.components.get(ref, {}).get("value", ""))
                is_link = r is not None and r <= _LINK_OHMS
            groups: dict[str, list[dict]] = defaultdict(list)
            for p in pins:
                m = _ELEMENT_RE.match(p["function"])
                groups[m.group(1) if m else ref].append(p)
            # A common-mode choke is two lines in one 4-pin part with no element
            # names to group by. Pins 1-2 are one line and 3-4 the other, which
            # the netlist bears out: FL1 has PHY_D+/AUX_D+ on 1-2 and the D-
            # pair on 3-4.
            for key, group in list(groups.items()):
                if len(group) == 4 and ref.startswith(("FL", "FB")):
                    by_pin = {p["pin"]: p for p in group}
                    if {"1", "2", "3", "4"} <= by_pin.keys():
                        del groups[key]
                        groups[f"{key}.a"] = [by_pin["1"], by_pin["2"]]
                        groups[f"{key}.b"] = [by_pin["3"], by_pin["4"]]
            for group in groups.values():
                if len(group) != 2:
                    continue  # not a two-terminal element; leave it alone
                a, b = group[0]["net"], group[1]["net"]
                if is_power(a) and is_power(b):
                    continue
                if is_power(a):
                    self.pullups[b].add(a)
                elif is_power(b):
                    self.pullups[a].add(b)
                elif is_link:
                    self._union(a, b)

    def group_of(self, net: str) -> str:
        return self.find(net)

    def supplies(self, ref: str) -> set[str]:
        """Power nets feeding this component's power_in pins, GND excluded."""
        out = set()
        for p in self.pins.get(ref, []):
            if p["type"] == "power_in" and is_power(p["net"]):
                name = leaf(p["net"])
                if not name.startswith(("GND", "VSS", "AGND")):
                    out.add(name)
        return out

    def crossing(self, a: str, b: str) -> list[dict]:
        """Signal nets that reach both components, following series passives.

        Returns one entry per merged net: the names it goes by, and the pins it
        lands on at each end.
        """
        def by_group(ref):
            g = defaultdict(list)
            for p in self.pins.get(ref, []):
                if is_power(p["net"]):
                    continue
                g[self.group_of(p["net"])].append(p)
            return g

        ga, gb = by_group(a), by_group(b)
        out = []
        # Sorted throughout: a set's iteration order changes between runs, and
        # this file is checked in — a re-run must produce byte-identical output
        # or the diff is noise.
        for group in sorted(ga.keys() & gb.keys()):
            names = {leaf(p["net"]) for p in ga[group] + gb[group]}
            out.append({
                "group": group,
                "names": sorted(names, key=lambda n: (len(n), n)),
                "a_pins": ga[group],
                "b_pins": gb[group],
            })
        return sorted(out, key=lambda e: e["names"][0])

    def shared_power(self, a: str, b: str) -> list[str]:
        """Supply nets both components sit on — the answer for a power link."""
        sa = {leaf(p["net"]) for p in self.pins.get(a, []) if is_power(p["net"])}
        sb = {leaf(p["net"]) for p in self.pins.get(b, []) if is_power(p["net"])}
        return sorted(sa & sb)


# ── Inference ─────────────────────────────────────────────────────────────────

def classify(names: list[str]) -> str:
    """Name an interface from the nets that make it up.

    Bus-prefixed names (`FPGA_JTAG.TCK`) are asked about the prefix first: the
    person who drew the schematic already grouped those signals and named the
    group, and that name beats anything guessed from a single pin.
    """
    votes: Counter[str] = Counter()
    for name in names:
        head, _, tail = name.partition(".")
        for candidate in ([head, tail] if tail else [name]):
            if not candidate:
                continue
            for rx, iface in _INTERFACE_RULES:
                if rx.search(candidate.upper()):
                    votes[iface] += 1
                    break
            else:
                continue
            break
    if not votes:
        return ""
    top = votes.most_common()
    lead = top[0][1]
    return " + ".join(sorted(i for i, n in top if n == lead or n >= lead / 2))


def direction(entries: list[dict], a_label: str, b_label: str) -> str:
    """Who drives, from the pin types KiCad recorded on each net."""
    a_out = b_out = 0
    for e in entries:
        atypes = {p["type"] for p in e["a_pins"]}
        btypes = {p["type"] for p in e["b_pins"]}
        if "output" in atypes and "input" in btypes:
            a_out += 1
        elif "output" in btypes and "input" in atypes:
            b_out += 1
    if a_out and not b_out:
        return f"{a_label}→{b_label}"
    if b_out and not a_out:
        return f"{b_label}→{a_label}"
    return f"{a_label}↔{b_label}"


def infer_voltage(nl: Netlist, a: str, b: str, entries: list[dict]) -> str:
    """The rail these signals swing to, or nothing.

    Three sources, best first: a pull-up on one of the nets (that resistor is
    tied to the signalling rail by definition); the one supply both components
    share; nothing.  Guessing an IO voltage from the FPGA's bank supplies is not
    attempted — an ECP5 has eight of them and the netlist does not say which pin
    is in which bank.
    """
    rails: Counter[str] = Counter()
    for e in entries:
        for p in e["a_pins"] + e["b_pins"]:
            for rail in nl.pullups.get(p["net"], ()):
                name = leaf(rail)
                # A pull-down says nothing about the swing; only the high rail
                # does. Ground kept turning up as the "voltage" without this.
                if name.startswith(("GND", "VSS", "AGND")):
                    continue
                rails[name] += 1
    if rails:
        return pretty_volts(rails.most_common(1)[0][0])
    shared = nl.supplies(a) & nl.supplies(b)
    if len(shared) == 1:
        return pretty_volts(next(iter(shared)))
    return ""


# ── Board file ────────────────────────────────────────────────────────────────

def enrich(board: dict, nl: Netlist, warn) -> tuple[int, int]:
    nodes = {n["id"]: n for n in board["nodes"]}

    for node in board["nodes"]:
        ref = node.get("kicad_ref")
        if ref and ref not in nl.components:
            warn(f"node {node['id']!r}: kicad_ref {ref} is not in the netlist")

    n_nodes = 0
    for node in board["nodes"]:
        ref = node.get("kicad_ref")
        comp = nl.components.get(ref) if ref else None
        if not comp:
            continue
        info = node.setdefault("info", {})
        if comp["value"] and not re.fullmatch(r"[RCL]", comp["value"]):
            info.setdefault("partNumber", comp["value"])
        if comp["description"]:
            info["description"] = comp["description"]
        if comp["datasheet"] and comp["datasheet"] != "~":
            info["datasheet"] = comp["datasheet"]
        # Connectors are the nodes whose pinout a person actually wants.
        if ref.startswith("J") and not info.get("pins"):
            pins = []
            for p in sorted(nl.pins[ref], key=lambda p: _pin_key(p["pin"])):
                name = leaf(p["net"])
                pins.append({
                    "number": _pin_number(p["pin"]),
                    "name": p["function"] or p["pin"],
                    "signal": name,
                    "type": ("gnd" if name.startswith(("GND", "VSS"))
                             else "power" if is_power(name)
                             else "nc" if name.startswith("unconnected-")
                             else "signal"),
                })
            if pins:
                info["pins"] = pins
        n_nodes += 1

    n_conns = 0
    for conn in board["connections"]:
        a_node, b_node = nodes.get(conn["fromId"]), nodes.get(conn["toId"])
        if not a_node or not b_node:
            warn(f"connection {conn['fromId']}→{conn['toId']}: unknown node")
            continue
        a, b = a_node.get("kicad_ref"), b_node.get("kicad_ref")
        if not a or not b or a not in nl.components or b not in nl.components:
            continue  # a logical edge (firmware, host software); nothing to say

        entries = nl.crossing(a, b)
        if not entries:
            shared = [s for s in nl.shared_power(a, b)
                      if not s.startswith(("GND", "VSS"))]
            if shared:
                conn["interface"] = "power"
                conn["nets"] = nl.shared_power(a, b)
                conn["voltage"] = pretty_volts(shared[0])
                conn["signal_type"] = f"shared supply {conn['voltage']}"
                conn["direction"] = ""
            else:
                warn(f"connection {conn['fromId']}→{conn['toId']} "
                     f"({a}↔{b}): no net reaches both, not even a supply")
            n_conns += 1
            continue

        names = [e["names"][0] for e in entries]
        iface = classify(names) or conn.get("label", "")
        volts = infer_voltage(nl, a, b, entries)
        conn["interface"] = iface
        conn["nets"] = sorted(names)
        conn["voltage"] = volts
        conn["direction"] = direction(entries, a_node["label"], b_node["label"])
        bits = [iface] if iface else []
        if volts:
            bits.append(volts)
        bits.append(f"{len(names)} net{'s' if len(names) != 1 else ''}")
        conn["signal_type"] = " · ".join(bits)
        n_conns += 1

    return n_nodes, n_conns


def _pin_key(pin: str):
    return (0, int(pin)) if pin.isdigit() else (1, pin)


def _pin_number(pin: str):
    return int(pin) if pin.isdigit() else pin


# ── CLI ───────────────────────────────────────────────────────────────────────

def export_netlist(kicad_dir: Path, out: Path) -> Path:
    if not shutil.which("kicad-cli"):
        sys.exit("kicad-cli not on PATH; pass --netlist with an exported XML")
    sch = sorted(kicad_dir.glob("*.kicad_sch"))
    root = next((s for s in sch if s.stem == kicad_dir.name.replace(
        "awto-", "").replace("-hardware", "")), None)
    root = root or (kicad_dir / "cynthion.kicad_sch")
    if not root.exists():
        sys.exit(f"no root schematic in {kicad_dir}")
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"kicad-cli sch export netlist {root.name} → {out}", file=sys.stderr)
    subprocess.run(["kicad-cli", "sch", "export", "netlist",
                    "--format", "kicadxml", "-o", str(out), str(root)],
                   check=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kicad", metavar="DIR",
                    help="KiCad project directory; the netlist is exported from it")
    ap.add_argument("--netlist", metavar="FILE", default=str(DEFAULT_NETLIST),
                    help="kicadxml netlist to read (or to write, with --kicad)")
    ap.add_argument("--board", metavar="FILE", default=str(DEFAULT_BOARD))
    ap.add_argument("--out", metavar="FILE",
                    help="output path (default: in place over --board)")
    ap.add_argument("--check", action="store_true",
                    help="report only; write nothing")
    args = ap.parse_args()

    netlist = Path(args.netlist)
    if args.kicad:
        netlist = export_netlist(Path(args.kicad).expanduser(), netlist)
    if not netlist.exists():
        sys.exit(f"no netlist at {netlist}; pass --kicad DIR to export one")

    warnings: list[str] = []
    nl = Netlist(netlist)
    print(f"{len(nl.components)} components, {len(nl.nets)} nets",
          file=sys.stderr)

    board_path = Path(args.board)
    board = json.loads(board_path.read_text())
    n_nodes, n_conns = enrich(board, nl, warnings.append)

    for w in warnings:
        print(f"  warning: {w}", file=sys.stderr)
    print(f"enriched {n_nodes} nodes, {n_conns} connections "
          f"({len(warnings)} warnings)", file=sys.stderr)

    for conn in board["connections"]:
        if conn.get("signal_type"):
            print(f"  {conn['fromId']:12s}→ {conn['toId']:14s} "
                  f"{conn['signal_type']}", file=sys.stderr)

    if args.check:
        return 0
    out = Path(args.out) if args.out else board_path
    out.write_text(json.dumps(board, indent=2, ensure_ascii=False) + "\n")
    print(f"written {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
