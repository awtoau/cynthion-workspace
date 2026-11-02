#!/usr/bin/env python3
"""Rewrite a yosys synth_ecp5 EDIF into Diamond's primitive vocabulary.

Handoff blockers 1-3 are in docs/upstream-yosys-edif-notes.md; this is the
fourth wall behind them (#110): yosys writes Trellis-flavoured primitives and
property types that Diamond's ngdbuild rejects.

    ./scripts/diamond/edif_diamondize.py in.edf out.edf

Log: tmp/logs/edif_diamondize.log
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "tmp" / "logs" / "edif_diamondize.log"

INT_PROP = re.compile(r"\(property (INIT|INIT0|INIT1) \(integer (-?\d+)\)\)")
DPR_INIT = re.compile(r'\(property INITVAL \(string "64\'h([0-9a-fA-F]+)"\)\)')


def hexprop(m):
    """Diamond reads LUT/CCU2C INIT as a 0x-prefixed hex string, not an integer."""
    name = "init" if m.group(1) == "INIT" else m.group(1)
    return f'(property {name} (string "0x{int(m.group(2)) & 0xFFFF:04X}"))'


def instance_ids(text, cell):
    """Ids of instances whose cellRef is `cell` (yosys names them idNNNNN)."""
    pat = (r'\(instance \(rename (id\d+) "[^"]*"\)\s*\(viewRef VIEW_NETLIST '
           r'\(cellRef ' + cell + r' \(libraryRef LIB\)\)\)')
    return set(re.findall(pat, text))


def split_dpr_ports(text, ids):
    """DPR16X4C has scalar pins DI0..3 etc; yosys wrote 4-bit arrays.

    EDIF member index k is bit (3 - k) -- yosys writes the MSB first.
    """
    n = 0

    def ref(m):
        nonlocal n
        if m.group(3) in ids:
            n += 1
            return f"(portRef {m.group(1)}{3 - int(m.group(2))} (instanceRef {m.group(3)})"
        return m.group(0)

    text = re.sub(r"\(portRef \(member (DI|WAD|RAD|DO) (\d)\) \(instanceRef (id\d+)\)",
                  ref, text)
    for p in ("DI", "WAD", "RAD", "DO"):
        d = "OUTPUT" if p == "DO" else "INPUT"
        scal = " ".join(f"(port {p}{i} (direction {d}))" for i in range(4))
        text = re.sub(r"(cell DPR16X4C.*?)\(port \(array " + p + r" 4\) \(direction "
                      + d + r"\)\)", lambda m: m.group(1) + scal, text,
                      count=1, flags=re.S)
    return text, n


def map_io(text):
    """TRELLIS_IO -> Diamond IB/OB/OBZ/BB, which carry the pad on I or O."""
    kinds = {}
    for iid, direction in re.findall(
            r'\(instance \(rename (id\d+) "[^"]*"\)\s*\(viewRef VIEW_NETLIST '
            r'\(cellRef TRELLIS_IO \(libraryRef LIB\)\)\)\s*'
            r'\(property DIR \(string "(\w+)"\)\)\)', text):
        kinds[iid] = direction
    has_t = set(re.findall(r"\(portRef T \(instanceRef (id\d+)\)", text))
    new = {}
    for iid, dr in kinds.items():
        new[iid] = ("IB" if dr == "INPUT" else "BB" if dr == "BIDIR"
                    else "OBZ" if iid in has_t else "OB")

    def inst(m):
        return (m.group(1) + f"(cellRef {new[m.group(2)]} (libraryRef LIB)))")

    text = re.sub(r'(\(instance \(rename (id\d+) "[^"]*"\)\s*\(viewRef VIEW_NETLIST )'
                  r'\(cellRef TRELLIS_IO \(libraryRef LIB\)\)\)\s*'
                  r'\(property DIR \(string "\w+"\)\)\)',
                  lambda m: inst(m) + ")" if m.group(2) in new else m.group(0), text)

    def pin(m):
        iid, port = m.group(2), m.group(1)
        k = new.get(iid)
        if k == "IB" and port == "B":
            port = "I"
        elif k in ("OB", "OBZ") and port == "B":
            port = "O"
        return f"(portRef {port} (instanceRef {iid})"

    text = re.sub(r"\(portRef (\w+) \(instanceRef (id\d+)\)", pin, text)
    # IB/OB declarations may be absent from the yosys library section.
    decl = ""
    for name, ports in (("OB", "I:INPUT O:OUTPUT"),):
        if f"(cell {name}\n" not in text:
            p = " ".join(f"(port {a.split(':')[0]} (direction {a.split(':')[1]}))"
                         for a in ports.split())
            decl += (f"    (cell {name}\n      (cellType GENERIC)\n      (view VIEW_NETLIST\n"
                     f"        (viewType NETLIST)\n        (interface {p})\n      )\n    )\n")
    if decl:
        text = text.replace("    (cell TRELLIS_IO\n", decl + "    (cell TRELLIS_IO\n", 1)
    return text, len(new)


def lut_function(init):
    """Diamond ORCALUT4 takes a sum-of-products string, not a truth table."""
    if init == 0:
        return "0"
    if init == 0xFFFF:
        return "1"
    terms = []
    for i in range(16):
        if init >> i & 1:
            terms.append(" ".join(("" if i >> b & 1 else "!") + "ABCD"[b]
                                  for b in range(4)))
    return "+".join(f"({t})" for t in terms)


LUT_RE = re.compile(r"\(cellRef LUT4 \(libraryRef LIB\)\)\)(\s*)"
                    r"\(property INIT \(integer (\d+)\)\)")


def map_lut(text):
    new, n = LUT_RE.subn(
        lambda m: "(cellRef ORCALUT4 (libraryRef LIB)))" + m.group(1)
        + f'(property lut_function (string "{lut_function(int(m.group(2)))}"))',
        text)
    decl = ("    (cell ORCALUT4\n      (cellType GENERIC)\n      (view VIEW_NETLIST\n"
            "        (viewType NETLIST)\n        (interface (port A (direction INPUT)) "
            "(port B (direction INPUT)) (port C (direction INPUT)) "
            "(port D (direction INPUT)) (port Z (direction OUTPUT)))\n      )\n    )\n")
    return new.replace("    (cell LUT4\n", decl + "    (cell LUT4\n", 1), n


INST_RE = re.compile(r"^(\s*)\(instance (?:\(rename (id\d+) \"[^\"]*\"\)|(\S+))\s*$")


def map_ff(text):
    """TRELLIS_FF -> FD1P3xx/FD1S3xx, with INV cells for active-low CE/CLK.

    Flip-flop type follows REGSET (reset/set value), SRMODE (async or sync
    LSR) and whether LSR is wired; CEMUX=1 drops the enable (FD1S3*).
    """
    refs = set(re.findall(r"\(portRef (\w+) \(instanceRef (\S+?)\)\)", text))
    lines = text.split("\n")
    out, ffs, inv_inst, inv_net = [], {}, [], []
    stats = {"ff": 0, "inv": 0, "inv_in_sub": 0}
    i = 0
    while i < len(lines):
        m = INST_RE.match(lines[i])
        if m and i + 1 < len(lines) and "cellRef TRELLIS_FF " in lines[i + 1]:
            iid = m.group(2) or m.group(3)
            j, props = i + 2, {}
            while j < len(lines) and lines[j].strip().startswith("(property"):
                k, v = re.findall(r'\(property (\w+) \(string "([^"]*)"', lines[j])[0]
                props[k] = v
                j += 1
            has_lsr = ("LSR", iid) in refs
            async_ = props.get("SRMODE") == "ASYNC"
            setv = props.get("REGSET") == "SET"
            if not has_lsr:
                kind = "AY" if setv else "AX"
            elif async_:
                kind = "BX" if setv else "DX"
            else:
                kind = "JX" if setv else "IX"
            en = props.get("CEMUX", "CE") != "1"
            cell = ("FD1P3" if en else "FD1S3") + kind
            ffs[iid] = (props, cell, kind, en)
            out.append(lines[i])
            out.append(re.sub(r"cellRef TRELLIS_FF", "cellRef " + cell,
                              lines[i + 1]).rstrip() + ")")
            stats["ff"] += 1
            i = j
            continue
        out.append(lines[i])
        i += 1
    text = "\n".join(out)

    def pin(m):
        port, iid = m.group(1), m.group(2)
        if iid not in ffs:
            return m.group(0)
        props, cell, kind, en = ffs[iid]
        if port == "M":
            return ""
        if port == "CE" and not en:
            return ""
        if port == "LSR":
            port = "PD" if kind in ("BX", "JX") else "CD"
        port = {"CLK": "CK", "DI": "D", "CE": "SP"}.get(port, port)
        return f"(portRef {port} (instanceRef {iid}))"

    # Inverted CE / CLK: route the original net into an INV and the INV into the
    # flip-flop. The FF port keeps its Diamond name; nets are appended later.
    def inv(m):
        port, iid = m.group(1), m.group(2)
        if iid not in ffs:
            return m.group(0)
        props = ffs[iid][0]
        if (port == "CE" and props.get("CEMUX") == "INV") or \
                (port == "CLK" and props.get("CLKMUX") == "INV"):
            name = f"inv_{iid}_{port}"
            inv_inst.append(f'          (instance {name} (viewRef VIEW_NETLIST '
                            f'(cellRef INV (libraryRef LIB))))')
            tgt = "SP" if port == "CE" else "CK"
            inv_net.append(f"          (net {name}_o (joined (portRef Z "
                           f"(instanceRef {name})) (portRef {tgt} "
                           f"(instanceRef {iid}))))")
            stats["inv"] += 1
            return f"(portRef A (instanceRef {name}))"
        return m.group(0)

    text = re.sub(r"\(portRef (CE|CLK) \(instanceRef (\S+?)\)\)", inv, text)
    text = re.sub(r"\(portRef (\w+) \(instanceRef (\S+?)\)\)", pin, text)
    c = text.find("(contents")
    eol = text.find("\n", c)
    text = text[:eol + 1] + "\n".join(inv_inst) + "\n" + text[eol + 1:]
    end = text.rfind("\n        )\n      )\n    )")
    text = text[:end] + "\n" + "\n".join(inv_net) + text[end:]
    return text, stats


def map_ff_cells(text):
    """Apply map_ff per DESIGN cell: instance names repeat across modules."""
    head, body = text.split("(library DESIGN", 1)
    parts = re.split(r"(?=\n    \(cell )", body)
    total = {"ff": 0, "inv": 0}
    for n, part in enumerate(parts):
        if "(contents" in part and "TRELLIS_FF" in part:
            parts[n], st = map_ff(part)
            total["ff"] += st["ff"]
            total["inv"] += st["inv"]
    return head + "(library DESIGN" + "".join(parts), total


def convert(text):
    counts = {}
    ids = instance_ids(text, "DPR16X4C") | instance_ids(text, "TRELLIS_DPR16X4")
    text, counts["lut"] = map_lut(text)
    new, counts["int_init"] = INT_PROP.subn(hexprop, text)
    new, counts["dpr_cell"] = re.subn(r"cellRef TRELLIS_DPR16X4\b", "cellRef DPR16X4C", new)
    new, counts["dpr_init"] = DPR_INIT.subn(
        lambda m: f'(property INITVAL (string "0x{m.group(1).upper()}"))', new)
    new, counts["const"] = re.subn(
        r"\(cellRef (GND|VCC) \(libraryRef LIB\)\)",
        lambda m: "(cellRef " + ("VLO" if m.group(1) == "GND" else "VHI")
        + " (libraryRef LIB))", new)
    new = re.sub(r"\(portRef [GP] \(instanceRef (GND|VCC)\)\)",
                 r"(portRef Z (instanceRef \1))", new)
    # DIAGNOSTIC: chain heads' constant CIN is dropped (Diamond forbids it); a
    # head that needed CIN=1 changes function.
    def nocin(m):
        return re.sub(r"\s*\(portRef CIN \(instanceRef \S+?\)\)", "", m.group(0))

    new, counts["cin_const"] = re.subn(
        r"\(net (?:GND_NET|VCC_NET) \(joined.*?\n\s*\)\n", nocin, new,
        flags=re.S)
    new, counts["dpr_ports"] = split_dpr_ports(new, ids)
    new, counts["io"] = map_io(new)
    # DIAGNOSTIC: yosys's 36-bit DP16KD is PDPW16KD in Diamond (different pins);
    # narrowed here so map proceeds. Function of that one BRAM is NOT preserved.
    new, counts["narrow36"] = re.subn(
        r"\(property DATA_WIDTH_([AB]) \(integer 36\)\)",
        r"(property DATA_WIDTH_\1 (integer 18))", new)
    new, counts["ff"] = map_ff_cells(new)
    # Hierarchy kept by yosys is declared in DESIGN; a LIB cellRef finds only an
    # empty interface stub, so ngdbuild leaves the block unexpanded.
    new, counts["bufcc"] = re.subn(
        r"\(cellRef (BufferCC\w*) \(libraryRef LIB\)\)", r"(cellRef \1)", new)
    new = re.sub(r"    \(cell BufferCC\w*\n.*?\n    \)\n", "", new, count=3,
                 flags=re.S)
    return new, counts


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    new, counts = convert(src.read_text())
    dst.write_text(new)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    msg = f"{src} -> {dst}: {counts}"
    LOG.write_text(msg + "\n")
    print(msg)


if __name__ == "__main__":
    main()
