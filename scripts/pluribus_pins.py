#!/usr/bin/env python3
#
# Generate a pluribus pin-annotation TSV from this repo's own board platform.
# SPDX-License-Identifier: BSD-3-Clause

"""`boards/cynthion-r1/pins.tsv` for pluribus, from `CynthionPlatformRev1D4`.

Pluribus lifts an FPGA bitstream into a queryable netlist, and needs a pin table
to name the pads: tile row/col/PIO for each ball, plus a label. Its checked-in
Cynthion table is four columns where the loader wants eight, so the lift aborts
before it starts --

    FATAL: boards/cynthion-r1/pins.tsv: bad pin row (need 8 tab-separated fields)

Hand-maintaining that table would mean transcribing 197 ball assignments and
keeping them in step with the platform. Both halves are already machine-readable:

- **which ball carries which signal** -- this repo's platform, the same file the
  gateware builds against, so the table cannot drift from what was synthesised;
- **which tile a ball is** -- pluribus's own `device-db/ECP5/<device>/iodb.json`,
  which is silicon data and belongs to it, not to us.

So this generates rather than transcribes.

    scripts/pluribus_pins.py                       # write into the pluribus tree
    scripts/pluribus_pins.py -o tmp/pins.tsv       # somewhere else first
    scripts/pluribus_pins.py --device LFE5U-25F    # the die, not the marking

Note the device split, because it will bite someone: the part is **marked**
`LFE5U-12F` and pluribus's `board.toml` says so, but it is a **25F die** -- this
project established that, and the open flow places against the full 24,288 LUT4
and 56 EBR. The two `iodb.json` files agree on CABGA256 ball positions, so either
works for pin mapping; the metadata header follows `--device` so the loader's
own device check stays honest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLURIBUS = Path("/mnt/2tb/git/pluribus")

# What each signal is for, when the resource name alone does not say. Keyed by
# the resource name the platform uses.
FUNCTION = {
    "clk_60MHz": "60 MHz oscillator -- every clock on the board derives from it",
    "ram": "HyperRAM W956A8MBYA6I -- see docs/chips/hyperram/",
    "ulpi": "USB3343 ULPI PHY",
    "led": "status LED, active low",
    "user_io": "PMOD / user I/O header",
    "self_program": "active-low; asserting re-enters the bootloader",
    "spi_flash": "W25Q32JV configuration flash",
}


def platform_pins(platform_name: str):
    """(ball, label, direction) for every pad the platform assigns."""
    sys.path.insert(0, str(ROOT / "gateware"))
    from amaranth.build import DiffPairs, Pins

    import board  # noqa: F401  -- registers the platform package

    from board.cynthion_r1_4 import CynthionPlatformRev1D4  # noqa: E402

    plat = CynthionPlatformRev1D4()
    # Pins declared against a Connector are header-relative ("pmod_0:1"), not
    # balls. The platform carries the map; without it every PMOD and mezzanine
    # pad is silently the wrong tile, or -- as here -- caught as an unknown ball.
    conn_map = {}
    for (cname, cnumber), conn in plat.connectors.items():
        for pin_name, ball in conn.mapping.items():
            conn_map[f"{cname}_{cnumber}:{pin_name}"] = ball

    def resolve(name):
        return conn_map.get(name, name)

    out = []

    def walk(name, number, obj, path):
        """Recurse a Resource/Subsignal tree down to its Pins."""
        for sub in getattr(obj, "ios", []):
            io = getattr(sub, "ios", None)
            if io is None:
                continue
            label = ".".join(filter(None, path + [getattr(sub, "name", "")]))
            for item in io:
                if isinstance(item, Pins):
                    for ball in item.names:
                        out.append((resolve(ball), label, item.dir))
                elif isinstance(item, DiffPairs):
                    for ball in item.p.names:
                        out.append((resolve(ball), label + ".p", item.dir))
                    for ball in item.n.names:
                        out.append((resolve(ball), label + ".n", item.dir))
                elif getattr(item, "ios", None):
                    walk(name, number, item, path + [getattr(sub, "name", "")])

    for (name, number), res in plat.resources.items():
        base = name if number in (0, None) else f"{name}{number}"
        # A Resource whose ios are Pins directly (no Subsignals).
        from amaranth.build import Subsignal
        if res.ios and not isinstance(res.ios[0], Subsignal):
            for item in res.ios:
                if isinstance(item, Pins):
                    for ball in item.names:
                        out.append((resolve(ball), base, item.dir))
                elif isinstance(item, DiffPairs):
                    for ball in item.p.names:
                        out.append((resolve(ball), base + ".p", item.dir))
                    for ball in item.n.names:
                        out.append((resolve(ball), base + ".n", item.dir))
        else:
            walk(name, number, res, [base])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--device", default="LFE5U-12F",
                    help="device name for the header and the iodb lookup "
                         "(default LFE5U-12F, the marking; the die is 25F)")
    ap.add_argument("--package", default="CABGA256")
    ap.add_argument("-o", "--out", type=Path,
                    default=PLURIBUS / "boards" / "cynthion-r1" / "pins.tsv")
    ap.add_argument("--pluribus", type=Path, default=PLURIBUS)
    args = ap.parse_args()

    iodb_path = args.pluribus / "device-db" / "ECP5" / args.device / "iodb.json"
    if not iodb_path.exists():
        raise SystemExit(f"{iodb_path} not found -- is --pluribus right?")
    balls = json.loads(iodb_path.read_text())["packages"][args.package]
    print(f"{iodb_path.parent.name}/{args.package}: {len(balls)} balls")

    assigned = platform_pins("CynthionPlatformRev1D4")
    print(f"platform assigns {len(assigned)} pads")

    # A ball the platform names but the package does not have is a real error --
    # it means the platform and the device database disagree, and every row after
    # it would be annotating the wrong tile.
    unknown = sorted({b for b, _, _ in assigned if b not in balls})
    if unknown:
        raise SystemExit(f"platform names balls absent from {args.package}: {unknown}")

    dirs = {"i": "in", "o": "out", "io": "bidir", "oe": "out", "-": "bidir"}
    rows = []
    for index, (ball, label, direction) in enumerate(sorted(assigned), start=1):
        pos = balls[ball]
        base = label.split(".")[0].rstrip("0123456789")
        rows.append("\t".join([
            str(index), str(pos["row"]), str(pos["col"]), pos["pio"],
            dirs.get(direction, "bidir"), f"{ball}:{label}",
            FUNCTION.get(base, ""), "10",
        ]))

    header = [
        "# Pluribus pin annotation file — Great Scott Gadgets Cynthion r1.4",
        "#",
        "# GENERATED by cynthion-workspace scripts/pluribus_pins.py -- do not hand-edit.",
        "# Ball assignments come from the platform the gateware is built against, so",
        "# this cannot drift from what was synthesised; tile row/col/pio come from",
        f"# pluribus's own device-db/ECP5/{args.device}/iodb.json.",
        "#",
        "# The part is MARKED LFE5U-12F and is a 25F die. Ball positions are identical",
        "# between the two iodb files, so this table is valid for either.",
        "#",
        f"# device:  {args.device}",
        f"# package: {args.package}",
        "# crystal: 60 MHz",
        "#",
        "# pin\trow\tcol\tpio\tdir\tlabel\tfunction\tconfidence",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(header + rows) + "\n")
    print(f"wrote {args.out} ({len(rows)} pins)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
