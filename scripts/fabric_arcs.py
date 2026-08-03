#!/usr/bin/env python3
#
# How much of the ECP5's routing did these bitstreams actually switch on?
# See awtoau/pluribus#101, and #116 here.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures routing-arc coverage from `top.config` files against the Trellis
database, rather than quoting a coverage figure from elsewhere.

Why measure it here
-------------------

A routing arc on an ECP5 is a mux selection: per destination wire, exactly one
source can be driving at a time. So a single configuration can never reach more
than a small share of the interconnect no matter how much logic it holds -- the
19,900 LUTs of the fabric test light up a lot of *tiles* and almost none of the
*arcs*. Reaching the rest means building the same design again with a different
placement, and the number that says how much that bought is a coverage share.

pluribus publishes a table of expected shares per configuration count. Quoting
it would make this run comparable to that model but not to the silicon: the
table was computed for its own generator, whose placements vary by construction,
while this sweep varies them by handing nextpnr a different placer seed. Those
are different mechanisms and there is no reason they should cover the same
ground. So the share is measured from the artefacts the tools actually emitted.

Where the numbers come from
---------------------------

  * numerator -- `arc: DEST SOURCE` lines under each `.tile NAME:TYPE` heading
    of `top.config`, the textual bitstream ecppack consumes. That is the
    placement and routing as the tools finally committed it.

  * denominator -- the `.mux DEST` sections of each tile type's `bits.db` in the
    Trellis database, which list every source a destination wire can select,
    crossed with `tilegrid.json` for this device to say how many of each tile
    type the die carries.

`.fixed_conn` entries are deliberately excluded from both: a hardwired
connection is not a mux, cannot be configured wrong, and cannot be exercised or
missed. Counting them would inflate the denominator with arcs no test could
ever fail to cover.

Two denominators, and they mean different things
------------------------------------------------

  per instance -- every arc of every tile on the die. The honest ceiling: what
    fraction of the physical mux inputs on this part were selected at least
    once.

  per tile type -- arcs collapsed to (type, dest, source). Every PLC2 shares one
    mux structure, so covering an arc in one PLC2 exercises that structure but
    not the other 1,000 instances of it. This is the number that says how much
    of the *design* of the interconnect was exercised. Neither denominator is
    inherently larger: one collapses repeated tile instances, while the other
    rewards reaching different physical copies of the same mux.

Both are reported, because quoting either alone overstates something.

    ./scripts/fabric_arcs.py tmp/fabric-coverage/seed-*/top.config
    ./scripts/fabric_arcs.py --device LFE5U-12F path/to/top.config
"""

import argparse
import collections
import hashlib
import heapq
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "fabric_arcs.log"

# The Trellis database that ships with the oss-cad-suite this project pins. The
# same database nextpnr-ecp5 and ecppack were built against, so the arc names in
# it are the arc names in the config -- no translation, and no chance of scoring
# against a different silicon revision's routing.
TRELLIS = Path.home() / "opt" / "oss-cad-suite" / "share" / "trellis" / "database"

TILE = re.compile(r"^\.tile\s+(\S+)")
ARC = re.compile(r"^arc:\s+(\S+)\s+(\S+)")


def tile_type_arcs(family="ECP5"):
    """Returns {tile type: {(dest, source), ...}} for every type in the database.

    Only `.mux` sections count. See the module docstring for why `.fixed_conn`
    is left out.
    """
    tiledata = TRELLIS / family / "tiledata"
    if not tiledata.exists():
        raise SystemExit(f"no Trellis tiledata at {tiledata}")

    types = {}
    for db in sorted(tiledata.glob("*/bits.db")):
        arcs = set()
        dest = None
        for line in db.read_text().splitlines():
            if line.startswith(".mux"):
                dest = line.split()[1]
                continue
            if line.startswith("."):
                # Any other section ends the mux block; .config, .config_enum
                # and .fixed_conn are all configuration, not routing.
                dest = None
                continue
            if dest and line.strip():
                arcs.add((dest, line.split()[0]))
        types[db.parent.name] = arcs
    return types


def device_tiles(device, family="ECP5"):
    """Returns {tile name: tile type} for one device, from its tilegrid."""
    grid = TRELLIS / family / device / "tilegrid.json"
    if not grid.exists():
        raise SystemExit(f"no tilegrid for {device} at {grid}")
    return {name: entry["type"]
            for name, entry in json.loads(grid.read_text()).items()}


def config_arcs(path):
    """Returns the set of ((tile, type), dest, source) triples a config selects.

    Read from the file rather than inferred: this is what ecppack was told to
    program, so an arc counted here is an arc the silicon was actually asked to
    switch on.
    """
    used = set()
    tile = tile_type = None
    for line in path.read_text().splitlines():
        match = TILE.match(line)
        if match:
            name = match.group(1)
            tile, _, tile_type = name.partition(":")
            continue
        match = ARC.match(line)
        if match and tile is not None:
            used.add((tile, tile_type, match.group(1), match.group(2)))
    return used


def coverage_keys(path, known_types=None):
    """Return the physical and tile-type coverage keys selected by *path*.

    If ``known_types`` is supplied, arcs absent from that database are omitted
    from both sets. They are diagnostics, not evidence that may inflate a
    coverage numerator whose denominator came from another database.
    """
    instance = set()
    tile_type = set()
    for tile, kind, dest, source in config_arcs(path):
        if known_types is not None and (dest, source) not in known_types.get(kind, ()):
            continue
        instance.add((tile, dest, source))
        tile_type.add((kind, dest, source))
    return instance, tile_type


def greedy_order(configs, known_types=None):
    """Order candidates by new type arcs, breaking ties by physical arcs.

    The lazy heap avoids rescoring every remaining candidate after every pick,
    so candidate pools in the thousands remain practical. All candidates are
    returned; this is an anytime order, not a lossy filter.
    """
    candidates = []
    heap = []
    for index, path in enumerate(configs):
        instance, tile_type = coverage_keys(path, known_types)
        candidates.append((path, instance, tile_type))
        heapq.heappush(heap, (-len(tile_type), -len(instance), index, 0))

    selected = []
    steps = []
    seen_instance = set()
    seen_type = set()
    generation = 0
    remaining = set(range(len(candidates)))
    while remaining:
        neg_type, neg_instance, index, scored_at = heapq.heappop(heap)
        if index not in remaining:
            continue
        path, instance, tile_type = candidates[index]
        new_type = len(tile_type - seen_type)
        new_instance = len(instance - seen_instance)
        if scored_at != generation:
            heapq.heappush(heap, (-new_type, -new_instance, index, generation))
            continue
        remaining.remove(index)
        selected.append(path)
        seen_type.update(tile_type)
        seen_instance.update(instance)
        steps.append({
            "config": str(path),
            "type_new": new_type,
            "type_total": len(seen_type),
            "instance_new": new_instance,
            "instance_total": len(seen_instance),
        })
        generation += 1
    return selected, steps


def database_digest(device, family="ECP5"):
    """SHA-256 of the Trellis files that define this coverage denominator."""
    paths = [TRELLIS / "devices.json",
             TRELLIS / family / device / "tilegrid.json"]
    paths.extend(sorted((TRELLIS / family / "tiledata").glob("*/bits.db")))
    digest = hashlib.sha256()
    for path in paths:
        relative = str(path.relative_to(TRELLIS)).encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.read_bytes())
    return digest.hexdigest()


def coverage(configs, device):
    """Cumulative coverage after each config, and the totals it is against.

    Returns a dict; `steps` is one entry per config in the order given, so the
    marginal value of each extra configuration is visible rather than only the
    total. A sweep whose fourth build adds no new arcs has learned that from
    this list, not from the headline.
    """
    types = tile_type_arcs()
    tiles = device_tiles(device)

    missing = sorted({t for t in tiles.values() if t not in types})
    total_instance = sum(len(types.get(t, ())) for t in tiles.values())
    total_type = sum(len(types[t]) for t in sorted(set(tiles.values()))
                     if t in types)

    seen_instance = set()
    seen_type = set()
    unknown = set()
    steps = []
    for path in configs:
        used = config_arcs(path)
        before_i, before_t = len(seen_instance), len(seen_type)
        for tile, tile_type, dest, source in used:
            # An arc the database does not know is an arc outside the
            # denominator, which would make the share a ratio of two
            # incompatible things. It should be impossible -- ecppack wrote this
            # config from this database -- so it is checked rather than assumed,
            # and would mean the toolchain and the database have diverged.
            if (dest, source) not in types.get(tile_type, ()):
                unknown.add((tile_type, dest, source))
                continue
            seen_instance.add((tile, dest, source))
            seen_type.add((tile_type, dest, source))
        steps.append({
            "config": str(path),
            "arcs": len(used),
            "instance_total": len(seen_instance),
            "instance_new": len(seen_instance) - before_i,
            "type_total": len(seen_type),
            "type_new": len(seen_type) - before_t,
        })

    return {
        "device": device,
        "trellis_database": str(TRELLIS),
        "trellis_coverage_digest": database_digest(device),
        "tiles": len(tiles),
        "tile_types": len(set(tiles.values())),
        "types_without_database": missing,
        "arcs_not_in_database": len(unknown),
        "total_instance_arcs": total_instance,
        "total_type_arcs": total_type,
        "covered_instance_arcs": len(seen_instance),
        "covered_type_arcs": len(seen_type),
        "instance_share": len(seen_instance) / total_instance if total_instance else 0.0,
        "type_share": len(seen_type) / total_type if total_type else 0.0,
        "steps": steps,
    }


def report(result, emit):
    emit(f"device {result['device']}: {result['tiles']} tiles of "
         f"{result['tile_types']} types")
    emit(f"Trellis coverage database digest: "
         f"{result['trellis_coverage_digest']}")
    if result["types_without_database"]:
        emit(f"  no bits.db for: {', '.join(result['types_without_database'])} "
             f"-- their arcs are in neither total")
    emit(f"configurable routing arcs on the die: "
         f"{result['total_instance_arcs']:,} per instance, "
         f"{result['total_type_arcs']:,} distinct per tile type")
    if result["arcs_not_in_database"]:
        emit(f"  WARNING: {result['arcs_not_in_database']} arcs used are not in "
             f"the database. They were excluded from coverage; the toolchain "
             f"and the Trellis database may have diverged")
    else:
        emit("  every arc used is one the database knows, so the share below "
             "is a ratio of like for like")
    emit()
    emit(f"{'config':<34} {'arcs':>9} {'cumulative':>11} {'new':>9} "
         f"{'per-instance':>13} {'per-type':>9}")
    for index, step in enumerate(result["steps"], 1):
        name = Path(step["config"]).parent.name or Path(step["config"]).name
        emit(f"{index:>2} {name:<31} {step['arcs']:>9,} "
             f"{step['instance_total']:>11,} {step['instance_new']:>9,} "
             f"{100.0 * step['instance_total'] / result['total_instance_arcs']:>12.2f}% "
             f"{100.0 * step['type_total'] / result['total_type_arcs']:>8.2f}%")
    emit()
    emit(f"{len(result['steps'])} configurations covered "
         f"{result['covered_instance_arcs']:,} of "
         f"{result['total_instance_arcs']:,} routing arcs "
         f"-- {100.0 * result['instance_share']:.1f}% per instance")
    emit(f"  and {result['covered_type_arcs']:,} of "
         f"{result['total_type_arcs']:,} distinct arcs per tile type "
         f"-- {100.0 * result['type_share']:.1f}%")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("configs", nargs="+", type=Path,
                        help="top.config files, in the order they were built")
    parser.add_argument("--device", default="LFE5U-12F")
    parser.add_argument("--json", type=Path, default=None,
                        help="also write the full result here")
    parser.add_argument("--greedy", action="store_true",
                        help="order candidates by new per-tile-type arcs, then "
                             "new physical arcs")
    args = parser.parse_args()

    for path in args.configs:
        if not path.exists():
            print(f"no config at {path}")
            return 1

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

        configs = args.configs
        if args.greedy:
            configs, _ = greedy_order(configs, tile_type_arcs())
        result = coverage(configs, args.device)
        report(result, emit)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(result, indent=2))
            emit(f"json: {args.json}")
        emit(f"log: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
