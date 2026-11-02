#!/usr/bin/env python3
"""Map once, then par at several seeds from an existing top.ngd (#110).

    ./scripts/diamond/par_seeds.py --dir tmp/diamond/synp --seeds 1 2 3 \
        [--lpf-extra 'USE PRIMARY NET "clki";']

Per seed: par -t N, then trce and bitgen. Reports cells and routing only.
Log: tmp/logs/par_seeds.log
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import flow  # noqa: E402

LOG = ROOT / "tmp" / "logs" / "par_seeds.log"


def sh(cmd, cwd, env, limit):
    """Run one engine; `limit` is 3x the first measured run (see flow.FIRST_RUN)."""
    t0 = time.monotonic()
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=limit)
    return p.returncode, p.stdout, time.monotonic() - t0


def say(msg):
    print(msg, flush=True)
    with LOG.open("a") as h:
        h.write(msg + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--lpf-extra", nargs="*", default=[])
    ap.add_argument("--lpf-sub", nargs="*", default=[],
                    help="OLD=NEW text substitutions in the .lpf (diagnostic)")
    ap.add_argument("--par-effort", default="5")
    ap.add_argument("--skip-map", action="store_true")
    a = ap.parse_args()
    d = a.dir.resolve()
    env = flow.diamond_env()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    lpf = d / "top_x.lpf"
    base = (d / "top.lpf").read_text()
    for sub in a.lpf_sub:  # DIAGNOSTIC: not the shipping pinout
        old, new = sub.split("=", 1)
        base = base.replace(old, new)
    lpf.write_text(base + "\n" +
                   "\n".join(a.lpf_extra) + "\n")
    A = ["-a", flow.ARCH]
    if not a.skip_map:
        rc, out, t = sh(["map", "top.ngd", lpf.name, *A, "-p", flow.DEVICE,
                         "-t", flow.PACKAGE, "-s", flow.SPEED, "-o", "top.ncd",
                         "-pr", "top.prf"], d, env, 1200)
        say(f"map rc={rc} {t:.0f}s")
        if rc:
            say(out[-2500:])
            return 1
    res = {}
    for s in a.seeds:
        out_ncd = f"top_par_s{s}.ncd"
        rc, out, t = sh(["par", "-w", "-l", a.par_effort, "-t", str(s),
                         "-n", "1", "top.ncd", out_ncd, "top.prf"],
                        d, env, 2400)
        (d / f"par_s{s}.log").write_text(out)
        unrouted = re.search(r"Number of unrouted conns = (\S+)", out)
        say(f"seed {s}: par rc={rc} {t:.0f}s unrouted="
            f"{unrouted.group(1) if unrouted else '?'}")
        res[s] = {"par_rc": rc, "par_s": round(t), "unrouted":
                  unrouted.group(1) if unrouted else None}
        if rc:
            res[s]["tail"] = out[-1500:]
            continue
        rc2, o2, t2 = sh(["trce", "-v", "10", "-o", f"top_s{s}.twr",
                          out_ncd, "top.prf"], d, env, 900)
        res[s]["trce_rc"] = rc2
        rc3, o3, t3 = sh(["bitgen", "-w", "-d", out_ncd], d, env, 600)
        res[s]["bitgen_rc"] = rc3
        say(f"seed {s}: trce rc={rc2} {t2:.0f}s bitgen rc={rc3} {t3:.0f}s")
    (d / "seeds.json").write_text(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
