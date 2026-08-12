#!/usr/bin/env python3
#
# Does Lattice Diamond place-and-route reach a higher clock than nextpnr on the
# RISC-V SoC?  See awtoau/cynthion-workspace#110.
# SPDX-License-Identifier: BSD-3-Clause

"""
Run the SoC (`gateware/soc/top.py`) through Diamond at a series of clock constraints and
compare against nextpnr on the same RTL.

## Why the question is worth asking

nextpnr on this design, same source, only the requested SYNC_MHZ varying:

| requested | achieved | outcome |
|---|---|---|
| 60 | 72.6 MHz | builds  |
| 80 | 76.3 MHz | builds  |
| 90 | 86.1 MHz | refused |
| 100| 92.0 MHz | refused |

The *achieved* figure rises with the request, so the placer works harder against
a tighter constraint and 92 MHz is where nextpnr stopped improving rather than a
wall in the silicon.  Diamond's timing-driven placer is generally stronger on
marginal designs, and the part has headroom -- the 12F-marked die is a 25F, and
20,143 LUT4s computed correctly at 86.43 MHz on it
(#116, pluribus#98) while this SoC uses 7,249.

## Which comparison is actually available

Only the whole-toolchain one.  Feeding the *same yosys netlist* into Diamond's
place-and-route -- which would isolate PAR from synthesis -- is structurally
impossible, and that is a settled finding rather than an untried idea: yosys
emits Project Trellis's own primitive vocabulary (`TRELLIS_DPR16X4`,
`TRELLIS_FF`, LUT4 `INIT`) which Diamond's `ngdbuild` has no cells for.  See
the pluribus repo's `docs/ecp5/diamond-par-isolation-blocked.md`.  This script
re-checks that blocker with `--check-edif` so the claim stays live rather than
inherited, then runs the comparison that is possible:

    nextpnr : yosys synth_ecp5 -> nextpnr-ecp5   (both open)
    diamond : Diamond LSE      -> Diamond map/par (both vendor)

A difference therefore names "the vendor toolchain", not "the vendor placer".

## What counts as a result

A timing report is a claim.  The bitstream is configured onto the board and the
console read, exactly as `riscv_clock_ladder.py` does it -- whose `configure`
and `verify` are imported rather than reimplemented, so both ladders judge a
frequency by the same standard.  Passing means the printed product is
`369d0368` (0x12345678 * 3, so the CPU is multiplying rather than echoing a
constant) *and* the tick counter advances between two separate reads.  A CPU
with marginal timing computes the wrong answer rather than stopping, so
checking the arithmetic is the whole point.

## The two traps

**`usb` must stay at 60 MHz** -- the ULPI PHY requires it.  Only `sync`/`fast`
vary, which `VariableClockDomainGenerator` already handles.

**`SidebandDebug` derives its baud from the clock**, so raising `sync` without
raising it gives a dead debug link rather than a slow one.  `top.py` derives
both from `SYNC_MHZ`, which comes from `CYNTHION_SYNC_MHZ`.

`--board` is opt-in and off by default.  Diamond is configured for the 25F die
this part actually is, so `bitgen` writes the 25F IDCODE while the part reports
the 12F one -- a Diamond bitstream is a timing result here, not something to
load.  The timing comparison needs no board.

    ./dev.py diamond ladder -- --check-edif
    ./dev.py diamond ladder -- --frequencies 90 100 110
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# scripts/diamond/<name>.py, so the repo root is three levels up. These lived in
# scripts/ and were moved; a stale `.parent.parent` put every log and artifact
# under scripts/tmp/ instead of tmp/, which is silent and wrong.
ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "tmp" / "logs" / "diamond_riscv_ladder.log"
RESULTS = ROOT / "tmp" / "diamond_riscv_ladder.json"
GATEWARE = ROOT / "gateware" / "soc" / "top.py"
WORK = ROOT / "tmp" / "diamond"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "gateware"))

from soc import variant  # noqa: E402

# The same verify the open-flow ladder uses.  Importing rather than
# reimplementing is deliberate: if the two ladders judged a frequency by
# different standards, a Diamond "win" could be nothing but a looser check.
from riscv_clock_ladder import verify


def rung_env(mhz):
    """The variant environment for one rung, and the build directory it names.

    `CYNTHION_SYNC_MHZ`, not a source rewrite: `top.py` reads the clock through
    `variant.value()`, so `riscv_clock_ladder.set_clock`'s regex matches nothing
    and every rung would build at the default (#439).  Each rung then has its
    own build directory (#351), so the artifacts do not overwrite each other.
    """
    env = dict(os.environ, CYNTHION_SYNC_MHZ=str(mhz))
    return env, variant.build_dir(ROOT, env)


def sh(command, cwd=ROOT, env=None):
    return subprocess.run(["bash", "-c", command], cwd=str(cwd), env=env,
                          capture_output=True, text=True)


def open_flow_build(mhz, env, build):
    """Build with yosys + nextpnr at `mhz`.  Returns (ok, achieved, detail).

    nextpnr's generated build script runs under `set -e` with no
    `--timing-allow-fail`, so a design that misses its constraint stops the
    build outright.  That is the "refused" in the table above: not an inability
    to place, but a refusal to emit a bitstream it cannot vouch for.  The
    achieved figure is still in `top.tim` either way, so it is parsed from
    there regardless of whether the build completed.

    A failure with no `top.tim` never reached nextpnr, and calling that a
    timing refusal invents a result: a missing firmware binary reported as all
    four rungs refused, in one second.
    """
    # Removed first, so a rerun of this rung cannot report the previous run's
    # number as this one's.
    timing = build / "top.tim"
    timing.unlink(missing_ok=True)

    result = sh(f'source "$HOME/opt/oss-cad-suite/environment" && '
                f'python3.15t {GATEWARE} --build', env=env)
    achieved = None
    if timing.exists():
        for line in timing.read_text().splitlines():
            found = re.search(r"Max frequency for clock\s+'\$glbnet\$clk':"
                              r"\s*([\d.]+)\s*MHz", line)
            if found:
                achieved = float(found.group(1))
    if result.returncode != 0:
        if achieved is None:
            tail = (result.stdout + result.stderr).strip().splitlines()
            return False, None, f"build failed before nextpnr: {tail[-1][:90]}"
        return False, achieved, "nextpnr refused (timing not met)"
    return True, achieved, "built"


def emit_sources(mhz, build):
    """Turn the Amaranth RTLIL the open flow just wrote into Verilog for LSE.

    Reusing that `.il` rather than re-elaborating is what makes this a
    comparison: both toolchains then start from byte-identical RTL, and any
    difference is the toolchain rather than two different elaborations.
    """
    outdir = WORK / f"src{mhz}"
    result = sh(f'source "$HOME/opt/oss-cad-suite/environment" && '
                f'python3 {ROOT}/scripts/emit_verilog.py '
                f'--il {build}/top.il --outdir {outdir} '
                f'--extra-verilog {build}/VexiiRiscv.v '
                f'--name emit_diamond{mhz}')
    behav = outdir / "behavioural.v"
    edif = outdir / "structural.edf"
    if not behav.exists():
        return None, None, result.stdout + result.stderr
    return behav, edif, None


def diamond_build(behav, mhz, build):
    """Diamond LSE synthesis + map + par + trce + bitgen at `mhz`.

    `--freq` appends a FREQUENCY NET constraint, which is what makes the
    reported number "what the tool worked toward" rather than "what fell out".
    An unconstrained Diamond run reports whatever it happened to reach and is
    not comparable to nextpnr's constrained figure.

    Note that VexiiRiscv.v is NOT passed as an extra source here: yosys read it
    in when producing behavioural.v and re-emitted the module, so passing it
    again is a duplicate definition and LSE stops with

        ERROR - synthesis: extra0.v(11603): duplicate module name VexiiRiscv.
    """
    outdir = WORK / f"lse{mhz}"
    result = sh(f'python3 {ROOT}/scripts/diamond/flow.py '
                f'--verilog {behav} --lpf {build}/top.lpf --mode lse '
                f'--outdir {outdir} --freq {mhz} --name diamond_lse{mhz}')
    res = outdir / "result.json"
    if result.returncode != 0 or not res.exists():
        text = (result.stdout + result.stderr).strip().splitlines()
        detail = next((l for l in reversed(text) if "ERROR" in l or "!!!" in l),
                      text[-1] if text else "diamond failed")
        return None, outdir, detail[:160]
    return json.loads(res.read_text()), outdir, None


def diamond_bitstream(outdir):
    """Find the .bit Diamond's bitgen produced, if it got that far."""
    bits = sorted(outdir.glob("*.bit"))
    return bits[0] if bits else None


def check_edif(behav_edif, mhz, build, emit):
    """Re-check the PAR-isolation blocker rather than inheriting the claim.

    The finding is that Diamond's ngdbuild cannot read a yosys ECP5 netlist
    because the two tools do not share a primitive vocabulary.  It is a strong
    claim to rest a negative result on, so it is re-run rather than cited.
    """
    outdir = WORK / f"yospar{mhz}"
    result = sh(f'python3 {ROOT}/scripts/diamond/flow.py '
                f'--verilog {behav_edif} --lpf {build}/top.lpf --mode yosys '
                f'--outdir {outdir} --freq {mhz} --name diamond_yospar{mhz}')
    text = result.stdout + result.stderr
    errors = sorted({l.strip() for l in text.splitlines() if "ERROR -" in l})
    if result.returncode == 0:
        emit("  PAR isolation: ngdbuild ACCEPTED the yosys netlist -- the "
             "documented blocker has gone, and the stronger comparison is "
             "now available")
        return True, errors
    emit(f"  PAR isolation: still blocked, {len(errors)} distinct ngdbuild error(s)")
    for e in errors[:4]:
        emit(f"    {e[:150]}")
    return False, errors


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--frequencies", type=int, nargs="+",
                        default=[90, 100, 110])
    parser.add_argument("--check-edif", action="store_true",
                        help="re-check the yosys-netlist-into-Diamond-PAR blocker")
    parser.add_argument("--board", action="store_true",
                        help="configure and verify each bitstream on hardware")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    results = {}

    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        emit("Diamond vs nextpnr on the VexiiRiscv SoC  (#110)")
        emit("passing = printed product correct AND tick counter advancing;")
        emit("a marginal CPU computes the wrong answer rather than stopping.")
        emit()

        for mhz in args.frequencies:
            emit(f"=== {mhz} MHz")
            env, build = rung_env(mhz)
            emit(f"  variant {build.name}")

            ok, achieved, detail = open_flow_build(mhz, env, build)
            shown = f"{achieved:.1f} MHz" if achieved else "unknown"
            emit(f"  nextpnr: {detail}, achieved {shown}")
            row = {"nextpnr_built": ok, "nextpnr_achieved": achieved,
                   "variant": build.name}

            behav, edif, err = emit_sources(mhz, build)
            if not behav:
                emit(f"  verilog emit failed: {err[-200:] if err else ''}")
                results[mhz] = row | {"diamond": "verilog emit failed"}
                continue

            if args.check_edif:
                passed, errors = check_edif(edif, mhz, build, emit)
                row["par_isolation_possible"] = passed
                row["ngdbuild_errors"] = errors[:8]

            res, outdir, err = diamond_build(behav, mhz, build)
            if res is None:
                emit(f"  Diamond: FAILED -- {err}")
                results[mhz] = row | {"diamond_built": False,
                                      "diamond_detail": err}
                continue

            fmax = res.get("fmax", {})
            emit(f"  Diamond: built, fmax {fmax}, util {res.get('util')}, "
                 f"{res.get('seconds')}s")
            row |= {"diamond_built": True, "diamond_fmax": fmax,
                    "diamond_util": res.get("util"),
                    "diamond_seconds": res.get("seconds")}
            results[mhz] = row

            if not args.board:
                continue

            bit = diamond_bitstream(outdir)
            if not bit:
                emit("  Diamond: no bitstream emitted -- nothing to verify")
                results[mhz] = row | {"hardware": "no bitstream"}
                continue

            emit(f"  configuring {bit.name}")
            if not configure_bit(bit):
                emit("  configure FAILED")
                results[mhz] = row | {"hardware": "configure failed"}
                continue
            good, detail = verify()
            emit(f"  hardware: {'PASS' if good else '*** FAIL'}  {detail}")
            results[mhz] = row | {"hardware_pass": good,
                                  "hardware_detail": detail}

        RESULTS.write_text(json.dumps(results, indent=2, default=str) + "\n")
        emit(f"results {RESULTS}")
        emit(f"log {LOG}")
    return 0


def configure_bit(bit):
    """Configure an arbitrary bitstream, not just the open flow's fixed path.

    `riscv_clock_ladder.configure()` hardcodes the Amaranth build output, which
    is right for that ladder and wrong here -- Diamond writes its own .bit
    elsewhere.
    """
    result = subprocess.run(
        [sys.executable,
         str(ROOT / "repos" / "apollo" / "apollo_fpga" / "commands" / "cli.py"),
         "configure", str(bit)],
        cwd=str(ROOT), capture_output=True, text=True)
    return result.returncode == 0


if __name__ == "__main__":
    sys.exit(main())
