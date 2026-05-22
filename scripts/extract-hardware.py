#!/usr/bin/env python3
"""
extract-hardware.py — KiCad schematic parser for cynthion.json

Reads KiCad 6/7 .kicad_sch files, extracts component info (reference,
value, description, part number, pin nets), and merges the results into
the board topology JSON under each node's "info" section where the node's
"kicad_ref" field matches a schematic reference.

Usage:
    python3 scripts/extract-hardware.py \
        --kicad ~/git_mirror/cynthion-hardware \
        --board app/assets/hardware/cynthion.json \
        --out   app/assets/hardware/cynthion.json   # update in place

    # Dry-run (print extracted catalog only):
    python3 scripts/extract-hardware.py \
        --kicad ~/git_mirror/cynthion-hardware \
        --catalog-only
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# KiCad s-expression parser (minimal — handles the property/pin subset we need)
# ---------------------------------------------------------------------------

def _parse_string(text: str, pos: int) -> tuple[str, int]:
    """Parse a quoted string starting at pos, return (value, end_pos)."""
    assert text[pos] == '"', f"Expected '\"' at {pos}, got {text[pos]!r}"
    i = pos + 1
    buf = []
    while i < len(text):
        c = text[i]
        if c == '\\' and i + 1 < len(text):
            buf.append(text[i + 1])
            i += 2
        elif c == '"':
            return ''.join(buf), i + 1
        else:
            buf.append(c)
            i += 1
    raise ValueError(f"Unterminated string at {pos}")


def _extract_properties(schematic_text: str) -> list[dict]:
    """
    Find all (symbol ...) blocks at the instance level (not inside lib_symbols)
    and extract their properties: Reference, Value, Description, Part Number,
    plus pin/net connections.
    Returns list of dicts with keys: ref, value, description, part_number, pins.
    """
    components = []

    # Split on symbol instances (lib_symbols block is near the top; skip it)
    # Strategy: find all `    (symbol (lib_id "` blocks (4-space indent = instance)
    instance_pattern = re.compile(
        r'  \(symbol \(lib_id "[^"]+"\)[^\n]*\n'   # opening line
    )

    # Extract all properties from the whole text using a simpler approach:
    # Find each instance block by looking for (property "Reference" "Xnnn")
    ref_pattern = re.compile(
        r'\(property "Reference" "([A-Z][A-Z0-9]*\d+)"'
    )

    # For each reference, find nearby properties in a window
    for ref_match in ref_pattern.finditer(schematic_text):
        ref = ref_match.group(1)
        # Skip generic symbols (no digit suffix) and power symbols
        if ref.startswith('#'):
            continue

        # Look backward ~3000 chars to find the symbol block start
        block_start = max(0, ref_match.start() - 200)
        # Look forward ~5000 chars for properties
        block = schematic_text[block_start: ref_match.start() + 6000]

        def get_prop(name: str) -> Optional[str]:
            m = re.search(r'\(property "' + re.escape(name) + r'" "([^"]*)"', block)
            return m.group(1) if m else None

        value = get_prop('Value') or ''
        description = get_prop('Description') or get_prop('Datasheet') or ''
        part_number = get_prop('Part Number') or get_prop('MPN') or ''

        # Skip template/generic entries (no real ref number)
        if not ref[1:].isdigit() and not re.match(r'[A-Z]+\d+', ref):
            continue

        # Extract pin-to-net connections for this symbol instance
        # Pins appear as (pin "net_name" (uuid ...)) or via wire net labels
        # Simpler: find (net (code N) (name "net_name")) patterns nearby
        # Most reliable: find hierarchical_label and label nets associated with pins
        pins = []

        # Look for pin numbers in the symbol definition
        pin_pattern = re.compile(
            r'\(pin\s+"([^"]*)"\s+\(uuid[^)]+\)\s*\)'
        )
        for pm in pin_pattern.finditer(block[:3000]):
            pins.append(pm.group(1))

        components.append({
            'ref': ref,
            'value': value,
            'description': description,
            'part_number': part_number,
            'pins': pins,
        })

    # Deduplicate by ref (take last occurrence, which is the instance not the lib def)
    seen: dict[str, dict] = {}
    for c in components:
        seen[c['ref']] = c
    return list(seen.values())


def _extract_connector_pins(schematic_text: str, ref: str) -> list[dict]:
    """
    For a given connector reference, find the net labels connected to each pin.
    Uses the hierarchical structure: find the symbol instance, then look for
    nearby net labels at the coordinates of each pin.
    """
    # Strategy: find (label "NET_NAME") near the reference in the schematic
    # This is heuristic — KiCad stores label positions, not pin-to-net mappings
    # directly in a human-parseable way without full netlist resolution.

    # Find all labels in a window around the reference
    ref_pos = schematic_text.find(f'"Reference" "{ref}"')
    if ref_pos < 0:
        return []

    window = schematic_text[max(0, ref_pos - 500): ref_pos + 8000]

    labels = re.findall(r'\(label "([^"]+)"', window)
    # Filter to labels that look like pin net names (not generic)
    pin_nets = [l for l in labels if not l.startswith('+') and l not in ('GND',)]

    result = []
    for i, net in enumerate(pin_nets, start=1):
        pin_type = 'gnd' if 'GND' in net else \
                   'power' if any(x in net for x in ('+3V3', '+5V', 'VCC', 'VBUS', 'VDD')) else \
                   'nc' if net in ('NC', 'RESERVED', '') else 'signal'
        result.append({
            'number': i,
            'name': net.split('_')[-1] if '_' in net else net,
            'signal': net,
            'type': pin_type,
        })
    return result


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract_catalog(kicad_dir: Path) -> dict[str, dict]:
    """
    Parse all .kicad_sch files in kicad_dir, return a catalog:
    { "J7": { "ref": "J7", "value": "PMOD_A", "description": "...", ... }, ... }
    """
    catalog: dict[str, dict] = {}

    sch_files = sorted(kicad_dir.glob('*.kicad_sch'))
    if not sch_files:
        print(f"WARNING: no .kicad_sch files found in {kicad_dir}", file=sys.stderr)
        return catalog

    for sch_file in sch_files:
        print(f"  parsing {sch_file.name}…", file=sys.stderr)
        text = sch_file.read_text(encoding='utf-8', errors='replace')
        components = _extract_properties(text)

        for comp in components:
            ref = comp['ref']
            # Enrich connectors with pin-to-net mapping
            if ref.startswith('J') and not comp['pins']:
                comp['pins'] = _extract_connector_pins(text, ref)
            catalog[ref] = comp

    return catalog


def merge_into_board(board_json: dict, catalog: dict[str, dict]) -> int:
    """
    For each node in board_json["nodes"] that has a "kicad_ref" field,
    populate/update its "info" section from the catalog.
    Returns count of nodes updated.
    """
    updated = 0
    for node in board_json.get('nodes', []):
        ref = node.get('kicad_ref')
        if not ref or ref not in catalog:
            continue
        comp = catalog[ref]

        info = node.setdefault('info', {})

        # Only overwrite if catalog has non-trivial data
        if comp.get('value') and comp['value'] not in ('R', 'C', 'L', 'TestPoint'):
            info.setdefault('partNumber', comp['value'])
        if comp.get('part_number'):
            info['partNumber'] = comp['part_number']
        if comp.get('description'):
            info.setdefault('description', comp['description'])

        # Merge pins — only if catalog found meaningful nets
        if comp.get('pins') and len(comp['pins']) > 1:
            existing_pins = info.get('pins', [])
            if not existing_pins:
                info['pins'] = comp['pins']
            else:
                # Merge signal names into existing pin list by index
                for i, cat_pin in enumerate(comp['pins']):
                    if i < len(existing_pins):
                        existing_pins[i].setdefault('signal', cat_pin.get('signal', ''))
                info['pins'] = existing_pins

        updated += 1
        print(f"  merged {ref} → node '{node['id']}'", file=sys.stderr)

    return updated


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--kicad', required=True, metavar='DIR',
                    help='Directory containing .kicad_sch files')
    ap.add_argument('--board', metavar='FILE',
                    help='Board JSON to update (read+write)')
    ap.add_argument('--out', metavar='FILE',
                    help='Output path (default: same as --board)')
    ap.add_argument('--catalog-only', action='store_true',
                    help='Print extracted catalog and exit (no JSON update)')
    args = ap.parse_args()

    kicad_dir = Path(args.kicad).expanduser()
    if not kicad_dir.is_dir():
        sys.exit(f"ERROR: --kicad dir not found: {kicad_dir}")

    print(f"Extracting from {kicad_dir}…", file=sys.stderr)
    catalog = extract_catalog(kicad_dir)
    print(f"Extracted {len(catalog)} components.", file=sys.stderr)

    if args.catalog_only:
        json.dump(catalog, sys.stdout, indent=2)
        return

    if not args.board:
        sys.exit("ERROR: --board FILE required (or use --catalog-only)")

    board_path = Path(args.board).expanduser()
    board_json = json.loads(board_path.read_text())

    n = merge_into_board(board_json, catalog)
    print(f"Updated {n} nodes.", file=sys.stderr)

    out_path = Path(args.out).expanduser() if args.out else board_path
    out_path.write_text(json.dumps(board_json, indent=2) + '\n')
    print(f"Written to {out_path}", file=sys.stderr)


if __name__ == '__main__':
    main()
