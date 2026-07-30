#!/usr/bin/env python3.15t
"""
Summarise a sweep: which opcodes did something, which returned data, which were inert.

Separates three categories that are easy to conflate:

  active     -- changed the status register. The command reached the configuration
                engine and altered its state.
  responsive -- returned non-zero data but changed no status. Reads a register.
  inert      -- returned zeros and changed nothing.

The IDCODE artifact is called out rather than reported as a status change. The
boundary-scan opcodes (EXTEST and relatives) leave the TAP such that the *following*
LSC_READ_STATUS returns the 32-bit IDCODE 0x21111043 instead of a status word. That is a
property of the TAP's instruction register, not of the configuration status register, and
counting it as "status changed" would be wrong.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

IDCODE = 0x21111043


def load(path):
    return json.loads(Path(path).read_text())


def classify(d):
    active, responsive, inert, artifact = {}, {}, {}, {}
    for p in d.get("probes", []):
        key = p.get("opcode_hex", "?")
        names = "/".join(p.get("names", []))
        sa = p.get("status_after")
        sb = p.get("status_before")
        resp = p.get("response", "") or ""
        nonzero = bool(resp) and set(resp) != {"0"}

        after_val = int(sa, 16) if sa else None

        if after_val == IDCODE:
            artifact.setdefault((key, names), []).append(p)
            continue

        if p.get("delta"):
            active.setdefault((key, names), []).append(p)
        elif nonzero:
            responsive.setdefault((key, names), []).append(p)
        else:
            inert.setdefault((key, names), []).append(p)
    return active, responsive, inert, artifact


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", nargs="+")
    args = ap.parse_args()

    for path in args.results:
        d = load(path)
        active, responsive, inert, artifact = classify(d)
        print(f"\n=== {Path(path).name}  state={d.get('state')} "
              f"baseline={d.get('baseline', {}).get('status')}")
        print(f"    probes={len(d.get('probes', []))} "
              f"active={len(active)} responsive={len(responsive)} "
              f"inert={len(inert)} idcode-artifact={len(artifact)}")

        if active:
            print("\n  ACTIVE (status register changed):")
            for (op, names), ps in sorted(active.items()):
                deltas = {json.dumps(p["delta"], sort_keys=True) for p in ps}
                for dj in deltas:
                    dd = json.loads(dj)
                    print(f"    {op} {names}")
                    print(f"        {ps[0]['status_before']} -> "
                          f"{ps[0]['status_after']}  {dd}")

        if responsive:
            print("\n  RESPONSIVE (returned data, no status change):")
            for (op, names), ps in sorted(responsive.items()):
                vals = sorted({p["response"] for p in ps if p.get("response")})
                print(f"    {op} {names}: {vals}")

        if artifact:
            print("\n  IDCODE ARTIFACT (status read returned IDCODE, not status --")
            print("  the TAP is left selecting the ID register; not a status change):")
            for (op, names), ps in sorted(artifact.items()):
                print(f"    {op} {names}")

        if inert:
            print(f"\n  INERT ({len(inert)} opcodes: zeros, no status change):")
            for (op, names), _ in sorted(inert.items()):
                print(f"    {op} {names}")


if __name__ == "__main__":
    main()
