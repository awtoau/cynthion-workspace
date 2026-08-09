#!/usr/bin/env python3
#
# Change the HyperRAM pins' DRIVE/PULLMODE/SLEWRATE/HYSTERESIS in a BUILT bitstream.
# SPDX-License-Identifier: BSD-3-Clause

"""
Move an FPGA pin electrical axis in seconds instead of a resynthesis. #311.

    ./scripts/hyperram_pin_patch.py --show
    ./scripts/hyperram_pin_patch.py ck.DRIVE=16
    ./scripts/hyperram_pin_patch.py dq.HYSTERESIS=ON rwds.HYSTERESIS=ON
    ./scripts/hyperram_pin_patch.py ck.DRIVE=4 --out tmp/pinpatch/ck4.bit

## Why this exists

`DRIVE`, `PULLMODE`, `SLEWRATE` and `HYSTERESIS` are `.config_enum` entries in
the Trellis PIO tiles -- five configuration-frame bits per pin for DRIVE, one for
HYSTERESIS. They are bitstream bits, so a running design cannot change them, but a
BUILT bitstream can be rewritten and reconfigured without going near yosys. That
is what turns four rebuilds into four patches, and what makes three axes nobody
has ever varied affordable at all.

Same trick as `scripts/bram_patch.py`, one layer down: rewrite `top.config`, then
re-run the build's own `ecppack` line.

## What it can do that the source cannot

`SLEWRATE` on **CK#**. Amaranth emits an LPF entry for `port.p` only, and nextpnr
writes SLEWRATE to the named PIO and never to the differential partner, so every
build so far has CK at FAST and CK# at the Trellis default SLOW. DRIVE is
mirrored to both halves and does not have the problem. Here both halves of a
differential pair are named explicitly, so the asymmetry can be closed or
deliberately reproduced. See #311.

## How a pin becomes a tile

Nothing is hardcoded; the chain is the bitstream's own build plus the Trellis DB.

| step                | source                                          |
|---------------------|-------------------------------------------------|
| device, package     | the `nextpnr-ecp5` line in `build_top.sh`       |
| net -> ball         | `LOCATE COMP ... SITE` in `top.lpf`             |
| ball -> row/col/pio | `<db>/<device>/iodb.json`                       |
| row/col/pio -> tile | nextpnr `ecp5/bitstream.cc:get_pio_tile`        |
| legal values        | `<db>/tiledata/<type>/bits.db` `.config_enum`   |

`get_pio_tile`'s rule for the left edge -- which is where every HyperRAM pin is --
is the PICL1-family tile one row DOWN from the PIO's own: pin `C3` is row 2 and
its attributes live in `MIB_R3C0:PICL1`.

## What makes a wrong result loud

- **The value must exist.** Checked against that tile type's `.config_enum` list
  before anything is written, so a typo cannot produce a bitstream whose bits
  nobody defined.
- **The pin must be in use.** A PIO with no `BASE_TYPE` in this bitstream is one
  the design does not drive, and patching it would be a setting with no pin.
- **Config diff.** Every line this rewrote is printed, so the text handed to
  `ecppack` is inspectable and must be only the settings that were asked for.
- **Round trip.** Both configs are packed and unpacked again, and the tiles that
  differ must be exactly the tiles that were targeted -- see `verify` for why
  that, and not "the enums read back as written", is the sound test.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "tmp" / "awto_soc" / "build"
OUT = ROOT / "tmp" / "pinpatch"

sys.path.insert(0, str(ROOT / "scripts"))

from bram_patch import Refuse, ecppack_command, rel, run_trellis  # noqa: E402
from devlog import emit  # noqa: E402

# The Trellis database that ships with the toolchain on the path. Same tree
# `ecppack` reads, so the enums here are the enums it will encode.
DB = Path(os.environ.get(
    "TRELLIS_DB",
    Path.home() / "opt" / "oss-cad-suite" / "share" / "trellis" / "database")) / "ECP5"

# nextpnr's `--<n>k` argument to the Trellis device directory.
DEVICE_OF = {"25k": "LFE5U-25F", "45k": "LFE5U-45F", "85k": "LFE5U-85F",
             "12k": "LFE5U-12F"}

# `get_pio_tile`, ecp5/bitstream.cc. The left/right edges hold all four PIOs'
# electrical configuration in one tile, one row down from the PIO itself.
PIO_TILE_TYPES = {
    "left": ("PICL1", "PICL1_DQS0", "PICL1_DQS3"),
    "right": ("PICR1", "PICR1_DQS0", "PICR1_DQS3"),
    "top_a": ("PIOT0",), "top_b": ("PIOT1",),
    "bottom_a": ("PICB0", "EFB0_PICB0", "EFB2_PICB0", "SPICB0"),
    "bottom_b": ("PICB1", "EFB1_PICB1", "EFB3_PICB1"),
}

# The other half of a differential pair, which the LPF never names because
# Amaranth emits no entry for it.
PAIR = {"PIOA": "PIOB", "PIOC": "PIOD"}

ASSIGN_RE = re.compile(r"^(\w+)\.([A-Z_0-9]+)=(\S+)$")
LOCATE_RE = re.compile(r'^LOCATE COMP "([^"]+)" SITE "([^"]+)";', re.M)
IOBUF_RE = re.compile(r'^IOBUF PORT "([^"]+)"([^;]*);', re.M)


def build_target(build_dir):
    """(device, package) from the build's own nextpnr line."""
    script = (build_dir / "build_top.sh").read_text()
    line = next((l for l in script.splitlines() if "--package" in l), "")
    device = next((DEVICE_OF[k] for k in DEVICE_OF if f"--{k}" in line), None)
    package = next((t for p, t in zip(line.split(), line.split()[1:])
                    if p == "--package"), None)
    if not device or not package:
        raise Refuse(f"no device/package in {build_dir}/build_top.sh: {line!r}")
    return device, package


def pio_tile(row, col, pio, grid, width, height):
    """The tile holding this PIO's electrical configuration."""
    if row == 0:
        want, col = (PIO_TILE_TYPES["top_a"], col) if pio == "PIOA" \
            else (PIO_TILE_TYPES["top_b"], col + 1)
    elif row == height - 1:
        want, col = (PIO_TILE_TYPES["bottom_a"], col) if pio == "PIOA" \
            else (PIO_TILE_TYPES["bottom_b"], col + 1)
    elif col == 0:
        want, row = PIO_TILE_TYPES["left"], row + 1
    elif col == width - 1:
        want, row = PIO_TILE_TYPES["right"], row + 1
    else:
        raise Refuse(f"({row},{col}) is not on an edge; it cannot be a PIO")

    prefix = f"R{row}C{col}:"
    for name in grid:
        head, _, kind = name.partition(":")
        if head.endswith(f"R{row}C{col}") and kind in want:
            return name
    raise Refuse(f"no {want} tile at {prefix} in the device grid")


def enum_spec(tile_type, key):
    """(legal values, default token) for `<key>` in this tile type.

    The default matters on the way back out: `ecpunpack` writes no line for a
    value that is the cleared state, so `HYSTERESIS=OFF` lands as an ABSENT line
    rather than as `OFF`. `DRIVE` has no default token at all, which is why an
    unset DRIVE appears nowhere in a bitstream.
    """
    text = (DB / "tiledata" / tile_type / "bits.db").read_text()
    match = re.search(rf"^\.config_enum {re.escape(key)}( \w+)?\n((?:\S.*\n)*)",
                      text, re.M)
    if not match:
        return set(), None
    values = {line.split()[0] for line in match.group(2).splitlines() if line.strip()}
    return values, (match.group(1) or "").strip() or None


def lpf_pins(lpf_text, resource):
    """{group: [(net, ball, differential)]} for one resource, from the LPF."""
    types = {net: attrs for net, attrs in IOBUF_RE.findall(lpf_text)}
    groups = {}
    for net, ball in LOCATE_RE.findall(lpf_text):
        if not net.startswith(resource + "__"):
            continue
        group = net[len(resource) + 2:].split("__")[0]
        differential = "LVCMOS33D" in types.get(net, "") or "LVDS" in types.get(net, "")
        groups.setdefault(group, []).append((net, ball, differential))
    return groups


def resolve(groups, iodb, grid, width, height):
    """{group: [(net, ball, tile, pio, partner)]}, differential halves included.

    `partner` marks the other half of a differential pair. It has no `BASE_TYPE`
    of its own -- nextpnr writes one for the named PIO only -- so it is the pin
    that the LPF cannot reach and this tool can.
    """
    out = {}
    for group, entries in sorted(groups.items()):
        for net, ball, differential in sorted(entries):
            site = iodb.get(ball)
            if site is None:
                raise Refuse(f"{ball} is not a pin of this package")
            pios = [(f"PIO{site['pio']}", False)]
            if differential:
                partner = PAIR.get(pios[0][0])
                if partner is None:
                    raise Refuse(f"{net} is differential on {pios[0][0]}, which "
                                 f"has no pair")
                pios.append((partner, True))
            tile = pio_tile(site["row"], site["col"], pios[0][0], grid,
                            width, height)
            for pio, partner in pios:
                out.setdefault(group, []).append((net, ball, tile, pio, partner))
    return out


TILE_RE = re.compile(r"^\.tile (\S+)\n((?:[^.\n].*\n)*)", re.M)


def parse_tiles(text):
    """{tile name: [body lines]} from a `.config`."""
    return {m.group(1): m.group(2).splitlines() for m in TILE_RE.finditer(text)}


def current(tiles, sites, keys):
    """What the bitstream says now: {(tile, pio, key): value or None}."""
    out = {}
    for tile, pio in sites:
        body = tiles.get(tile, [])
        for key in keys:
            match = next((l for l in body if l.startswith(f"enum: {pio}.{key} ")),
                         None)
            out[(tile, pio, key)] = match.split()[-1] if match else None
    return out


def rewrite(text, changes):
    """Return the config with `{(tile, pio, key): value}` applied.

    A key already present is replaced in place, and any duplicate of it dropped;
    nextpnr emits `PULLMODE` twice for a differential pair. A key absent is
    appended to that tile's block -- `.config_enum PIOA.DRIVE` carries no default
    token, so an unset DRIVE has no line to replace.
    """
    per_tile = {}
    for (tile, pio, key), value in changes.items():
        per_tile.setdefault(tile, {})[f"enum: {pio}.{key}"] = value

    written = set()

    def substitute(match):
        tile = match.group(1)
        if tile not in per_tile:
            return match.group(0)
        wanted = per_tile[tile]
        body, seen = [], set()
        for line in match.group(2).splitlines():
            head = " ".join(line.split()[:2]) if line.startswith("enum: ") else None
            if head in wanted:
                if head in seen:
                    continue                    # duplicate of one already set
                seen.add(head)
                written.add((tile, head))
                body.append(f"{head} {wanted[head]}")
            else:
                body.append(line)
        for head, value in wanted.items():
            if head not in seen:
                written.add((tile, head))
                body.append(f"{head} {value}")
        return f".tile {tile}\n" + "".join(l + "\n" for l in body if l.strip())

    out = TILE_RE.sub(substitute, text)
    missing = {(t, h) for t, keys in per_tile.items() for h in keys} - written
    if missing:
        raise Refuse(f"{len(missing)} settings did not reach a tile: "
                     f"{sorted(missing)[:4]}")
    return out


def unpack(bit, where):
    """`ecpunpack` a bitstream back to a `.config`, so it can be compared."""
    where.parent.mkdir(parents=True, exist_ok=True)
    result = run_trellis(["ecpunpack", str(bit), str(where)], where.parent)
    if result.returncode != 0:
        raise Refuse("ecpunpack failed:\n" + (result.stderr or result.stdout)[-600:])
    return parse_tiles(where.read_text())


def verify(before, after, changes):
    """Confine the damage: only the targeted tiles moved, and all of them did.

    NOT "the enums read back as written", which is not a sound test. Trellis
    decodes a PIO tile by best match over enums that SHARE bits: `BASE_TYPE
    BIDIR_LVCMOS33` includes `F3B1`, which is also `HYSTERESIS ON`, and
    `DRIVE 16` sets `F4B1 F5B1 F6B1` which `BASE_TYPE` also claims. So a
    legitimate change to one of them renames the others on read-back -- an
    LVCMOS33 pad decodes as `LVTTL33` (identical encoding) and a hysteresis-off
    bidir pad has no Trellis name at all. Requiring the old names back would fail
    every correct patch and pass a patch that changed nothing.

    What is checked instead is what a patch can actually get wrong: reaching a
    tile it was not asked to touch, or failing to reach one it was.
    """
    touched = {tile for tile, _, _ in changes}
    problems = []
    for tile in sorted(set(before) | set(after)):
        if set(before.get(tile, [])) == set(after.get(tile, [])):
            if tile in touched:
                problems.append(f"{tile}: targeted, but the bitstream is identical")
        elif tile not in touched:
            problems.append(f"{tile}: changed, and nothing asked it to")
    return problems


def config_delta(before_text, after_text):
    """Line-level difference between two `.config` files, as (tile, was, now)."""
    before, after = parse_tiles(before_text), parse_tiles(after_text)
    out = []
    for tile in sorted(set(before) | set(after)):
        was, now = set(before.get(tile, [])), set(after.get(tile, []))
        out += [(tile, line, None) for line in sorted(was - now)]
        out += [(tile, None, line) for line in sorted(now - was)]
    return out


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("assignments", nargs="*", metavar="GROUP.ATTR=VALUE",
                        help="e.g. ck.DRIVE=16 dq.HYSTERESIS=OFF; GROUP is a "
                             "subsignal of the resource, or `all`")
    parser.add_argument("--build-dir", type=Path, default=BUILD)
    parser.add_argument("--resource", default="ram_0",
                        help="the LPF net prefix to patch")
    parser.add_argument("--out", type=Path, default=None,
                        help="where the patched bitstream goes; defaults to "
                             "tmp/pinpatch/<assignments>.bit")
    parser.add_argument("--show", action="store_true",
                        help="print what the built bitstream says and stop")
    args = parser.parse_args()

    device, package = build_target(args.build_dir)
    iodb = json.loads((DB / device / "iodb.json").read_text())["packages"][package]
    grid = json.loads((DB / device / "tilegrid.json").read_text())
    # The device grid, from the tile names. `cols`/`rows` on a tile entry are its
    # own bit extent, not the die's -- reading those gives 106x12 on every part.
    positions = [re.search(r"R(\d+)C(\d+):", name) for name in grid]
    height = max(int(m.group(1)) for m in positions if m) + 1
    width = max(int(m.group(2)) for m in positions if m) + 1

    lpf = (args.build_dir / "top.lpf").read_text()
    config_path = args.build_dir / "top.config"
    config_text = config_path.read_text()
    tiles = parse_tiles(config_text)

    placed = resolve(lpf_pins(lpf, args.resource), iodb, grid, width, height)
    if not placed:
        raise Refuse(f"no {args.resource}__* pins in {args.build_dir}/top.lpf")

    keys = ("BASE_TYPE", "DRIVE", "PULLMODE", "SLEWRATE", "HYSTERESIS")
    emit(f"{device} {package}, {rel(args.build_dir)}")
    for group, entries in placed.items():
        for net, ball, tile, pio, partner in entries:
            state = current(tiles, [(tile, pio)], keys)
            if state[(tile, pio, "BASE_TYPE")] is None and not partner:
                raise Refuse(f"{net} ({ball}) is on {tile} {pio}, which this "
                             f"bitstream does not configure. Wrong build?")
            shown = "  ".join(f"{k}={state[(tile, pio, k)]}" for k in keys[1:])
            emit(f"  {group:6s} {net:22s} {ball:3s} {tile:18s} {pio}"
                 f"{'#' if partner else ' '} {shown}")
    if args.show or not args.assignments:
        return 0

    changes, labels = {}, []
    for assignment in args.assignments:
        match = ASSIGN_RE.match(assignment)
        if not match:
            raise Refuse(f"expected GROUP.ATTR=VALUE, got {assignment!r}")
        group, key, value = match.groups()
        targets = ([e for entries in placed.values() for e in entries]
                   if group == "all" else placed.get(group))
        if not targets:
            raise Refuse(f"no group {group!r}; have {sorted(placed)} or `all`")
        for _, _, tile, pio, _ in targets:
            kind = tile.partition(":")[2]
            legal, _default = enum_spec(kind, f"{pio}.{key}")
            if value not in legal:
                raise Refuse(f"{kind} {pio}.{key} takes {sorted(legal)}, "
                             f"not {value!r}")
            changes[(tile, pio, key)] = value
        labels.append(f"{group}-{key}-{value}")

    # Absolute: `ecppack` runs with the build directory as its cwd, so a relative
    # `--out` would be resolved against that and not against the caller's.
    out_bit = (args.out.resolve() if args.out
               else OUT / ("-".join(labels) + ".bit"))
    out_bit.parent.mkdir(parents=True, exist_ok=True)
    out_config = out_bit.with_suffix(".config")
    patched_text = rewrite(config_text, changes)
    out_config.write_text(patched_text)

    emit(f"\nconfig delta ({rel(out_config)}):")
    for tile, was, now in config_delta(config_text, patched_text):
        emit(f"  {tile:20s} {'-' + was if was else '+' + now}")

    # The baseline is a REPACK of the config this patch was derived from, not the
    # build's `top.bit`. A build whose nextpnr missed timing writes the config and
    # never reaches `ecppack`, so `top.bit` can be a placement or two old --
    # comparing against it would report the difference between two builds as if
    # this tool had caused it.
    base_bit = OUT / "baseline.bit"
    for config, bit in ((config_path, base_bit), (out_config, out_bit)):
        result = run_trellis(ecppack_command(args.build_dir, config, bit),
                             args.build_dir)
        if result.returncode != 0:
            raise Refuse(f"ecppack failed on {config}:\n"
                         + (result.stderr or result.stdout)[-600:])

    problems = verify(unpack(base_bit, OUT / "before.unpacked.config"),
                      unpack(out_bit, OUT / "after.unpacked.config"), changes)
    emit(f"\npatched {len(changes)} PIO settings -> {rel(out_bit)} "
         f"({out_bit.stat().st_size} bytes)")
    if problems:
        for problem in problems:
            emit(f"  ROUND TRIP: {problem}")
        raise Refuse(f"{len(problems)} round-trip differences; the bitstream is "
                     f"not what was asked for")
    emit(f"round trip: {len({t for t, _, _ in changes})} targeted tiles differ "
         f"in the packed bitstream, and no other tile does")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Refuse as refusal:
        emit(f"REFUSED: {refusal}")
        sys.exit(1)
