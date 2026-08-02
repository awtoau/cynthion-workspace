#!/usr/bin/env python3
#
# Raise the HyperRAM's clock until the data stops verifying, both PHYs.
# SPDX-License-Identifier: BSD-3-Clause

"""
Finds the clock the W956A8 actually stops working at, and what failure looks like.

    ./scripts/hyperram_ceiling.py --build        # bitstreams only, no board
    ./scripts/hyperram_ceiling.py --run          # board only, uses what is built
    ./scripts/hyperram_ceiling.py                # both

## The ladder is in device CK, so the two PHYs are comparable

`HyperRAMPHY` clocks the part at `sync`; `HyperRAMDQSPHY` clocks it at **twice**
`sync`, because `ODDRX2F` toggles the outgoing clock twice per `sync` cycle. A
sweep indexed by `sync` would compare a part running at 120 MHz against one
running at 240 and call it a PHY comparison. Every rung here is a CK, and both
PHYs are built for each rung -- non-DQS at `sync = CK`, DQS at `sync = CK/2`.

## Why a failure has to be characterised, not just counted

The recorded traps on this interface all produced *plausible wrong answers*
rather than failures, so "it passed" is a claim that needs support:

  * **BURSTDET** separates a working strobe from a fixed-latency count that
    happened to land right. A DQS rung that passes with BURSTDET clear has
    demonstrated nothing about DQS.
  * **The error count, not a flag.** One bad word in ten million and ten million
    bad words are different faults; only the second means "too fast".
  * **The first mismatch, kept with its address.** A half-word slip, a stuck
    lane and noise are told apart by *how* the value is wrong.
  * **Die temperature**, before and after, so a rung that failed while the part
    was hotter than the rung below is not read as a clock limit.

Counters are compared across a window rather than read once, so the rate is
measured over a known number of words instead of over "since configuration".
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "hyperram_ceiling.log"
BUILD_ROOT = ROOT / "tmp" / "hyperram-ceiling"
RESULTS = BUILD_ROOT / "results.json"
OSS_CAD = Path.home() / "opt" / "oss-cad-suite" / "bin"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "ecp5-test"))

from hyperram.hyperram_ceiling_top import (  # noqa: E402
    APPLET_ID, BURST_WORDS, DIE_PRESENT, REG_BAD_GOT, REG_BAD_INDEX,
    REG_BAD_WANT, REG_CAPTURE_ADDR, REG_CAPTURE_DATA, REG_CLOCK, REG_CONFIG,
    REG_DIE, REG_ERRORS, REG_ID, REG_READ_CYCLES, REG_STATUS, REG_WORDS,
    REG_WRITE_CYCLES, solve_pll)

CONFIGURE = [sys.executable,
             str(ROOT / "repos" / "apollo" / "apollo_fpga" / "commands"
                 / "cli.py"),
             "configure"]

# The ladder, in device CK. Starts at 120 -- the rate the non-DQS path is
# already verified at, so the sweep begins from a known-good rung rather than
# from a guess -- and every rung is reachable by BOTH paths (non-DQS needs
# `sync` = CK, DQS needs `sync` = CK/2 with an even CLKOP_DIV).
#
# The part is rated 166 MHz. Rungs above it are the question the user asked, not
# an assumption that they will work.
DEFAULT_CK = [120, 140, 150, 160, 168, 180, 192, 200, 210, 220, 240]

# Words to move through the counters before believing a rung. At roughly 40 M
# words/s this is a few seconds of device time and puts the error floor near
# 1e-8 per word -- low enough that "clean" means something, while staying far
# inside the 32-bit counters' wrap (about 4.3 G words).
DEFAULT_WORDS = 200_000_000

# A hard cap on JTAG polls so a stuck counter ends the rung instead of hanging.
# Not a duration: the loop exits on the word target, and this only bounds the
# pathological case where the target is never reached.
MAX_POLLS = 4000

MASK32 = 0xFFFF_FFFF


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def rungs(ck_list, paths):
    """(ck, dqs, sync) for each rung each PHY can actually reach."""
    out = []
    for ck in ck_list:
        for dqs in paths:
            sync = ck / 2 if dqs else float(ck)
            # Asked of the solver rather than of a table: a rung the PLL cannot
            # actually make would otherwise be built at whatever it produced
            # instead, and reported under the frequency it was asked for.
            if solve_pll(sync, fast_ratio=2 if dqs else None):
                out.append((float(ck), dqs, sync))
    return out


def tag_of(ck, dqs):
    return f"{'dqs' if dqs else 'sdr'}-ck{ck:g}"


def timing_of(build_dir):
    """nextpnr's verdict for this placement: (met, achieved_mhz, target_mhz)."""
    report = build_dir / "top.tim"
    if not report.exists():
        return None
    found = re.findall(r"Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz "
                       r"\((\w+) at ([\d.]+) MHz\)", report.read_text())
    if not found:
        return None
    # nextpnr prints this twice: an estimate after placement and the real figure
    # after routing. Taking the worst reports the placement estimate, which is
    # pessimistic by 15-25 MHz here -- the 150 MHz build reads 139 MHz FAIL
    # before routing and 166 MHz PASS after. The last one is the answer.
    name, achieved, verdict, target = found[-1]
    return (verdict == "PASS", float(achieved), float(target), name)


def build_one(ck, dqs, sync, handle):
    """Run yosys, nextpnr and ecppack for one rung. Never touches the board."""
    out = BUILD_ROOT / tag_of(ck, dqs)
    script = (
        "import sys\n"
        f"sys.path[:0]={[str(ROOT / 'ecp5-test'), str(ROOT / 'repos' / 'apollo')]!r}\n"
        "from hyperram.hyperram_ceiling_top import HyperRAMCeiling\n"
        "from cynthion_platform import CynthionPlatformRev1D4\n"
        f"d = HyperRAMCeiling(sync_mhz={sync!r}, dqs={dqs!r})\n"
        "CynthionPlatformRev1D4().build(d, build_dir=%r, do_program=False)\n"
        "print('BUILD OK')\n" % str(out)
    )
    env = dict(os.environ)
    env["PATH"] = f"{OSS_CAD}:{env['PATH']}"

    # The platform hardcodes `ecppack_opts` in its own `toolchain_prepare`, so
    # passing it as a build kwarg is a duplicate-argument TypeError. Amaranth
    # reads AMARANTH_<tool>_opts ahead of both, which is the one route that
    # reaches ecppack -- and it must repeat the platform's own flags, because it
    # replaces them rather than adding to them. Dropping --freq would silently
    # change the configuration clock.
    from build_helpers import ecppack_opts
    env["AMARANTH_ecppack_opts"] = ecppack_opts()["ecppack_opts"]
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                            capture_output=True, text=True)
    combined = (result.stdout or "") + (result.stderr or "")
    ok = result.returncode == 0 and "BUILD OK" in (result.stdout or "")
    if not ok:
        (out).mkdir(parents=True, exist_ok=True)
        (out / "build-failure.log").write_text(combined)
    return ok, out, combined


def delta(now, before):
    """`now - before` on a 32-bit counter that may have wrapped once."""
    return (now - before) & MASK32


def poll(dut, target_words, handle, sync_hz, bytes_per_word):
    """Accumulate a window of at least `target_words` and return what moved."""
    words0 = dut.registers.register_read(REG_WORDS)
    errors0 = dut.registers.register_read(REG_ERRORS)
    wcyc0 = dut.registers.register_read(REG_WRITE_CYCLES)
    rcyc0 = dut.registers.register_read(REG_READ_CYCLES)

    polls = 0
    while polls < MAX_POLLS:
        polls += 1
        words = dut.registers.register_read(REG_WORDS)
        if delta(words, words0) >= target_words:
            break
    errors = dut.registers.register_read(REG_ERRORS)
    wcyc = dut.registers.register_read(REG_WRITE_CYCLES)
    rcyc = dut.registers.register_read(REG_READ_CYCLES)

    dwords = delta(words, words0)
    derrors = delta(errors, errors0)
    dwcyc = delta(wcyc, wcyc0)
    drcyc = delta(rcyc, rcyc0)

    write_bps = (dwords * bytes_per_word) / (dwcyc / sync_hz) if dwcyc else 0.0
    read_bps = (dwords * bytes_per_word) / (drcyc / sync_hz) if drcyc else 0.0
    return {
        "words": dwords, "errors": derrors, "polls": polls,
        "write_cycles": dwcyc, "read_cycles": drcyc,
        "write_mbps": write_bps / 1e6, "read_mbps": read_bps / 1e6,
        "stalled": dwords < target_words,
    }


def run_one(ck, dqs, sync, target_words, handle):
    """Configure this rung and measure it. Returns a result dict."""
    tag = tag_of(ck, dqs)
    build_dir = BUILD_ROOT / tag
    bitstream = build_dir / "top.bit"
    result = {"ck_mhz": ck, "dqs": dqs, "sync_mhz": sync, "tag": tag,
              "verdict": "incomplete"}

    tim = timing_of(build_dir)
    if tim:
        result["timing"] = {"met": tim[0], "achieved_mhz": tim[1],
                            "target_mhz": tim[2], "clock": tim[3]}

    if not bitstream.exists():
        result["verdict"] = "no bitstream"
        emit(handle, f"  {tag}: no bitstream")
        return result

    configured = subprocess.run(CONFIGURE + [str(bitstream)], cwd=ROOT,
                                capture_output=True, text=True)
    if configured.returncode != 0:
        result["verdict"] = "configure failed"
        result["detail"] = (configured.stderr or "").strip()[-200:]
        emit(handle, f"  {tag}: configure FAILED -- {result['detail']}")
        return result

    from apollo_fpga import ApolloDebugger
    try:
        dut = ApolloDebugger()
    except Exception as exc:                    # noqa: BLE001
        result["verdict"] = "no debugger"
        result["detail"] = str(exc)[:200]
        emit(handle, f"  {tag}: Apollo did not attach -- {exc}")
        return result

    applet = dut.registers.register_read(REG_ID)
    if applet != APPLET_ID:
        # A wrong applet id means the board is running something else. Reporting
        # its numbers as this rung's would be the worst possible outcome.
        result["verdict"] = "wrong applet"
        result["detail"] = f"{applet:#010x}"
        emit(handle, f"  {tag}: applet {applet:#010x}, expected "
                     f"{APPLET_ID:#010x} -- refusing to measure")
        return result

    # The gateware states the clock it was built for. Checked against the host's
    # own idea rather than assumed: a rate computed from a requested frequency
    # that was not achieved is wrong by exactly the rounding error.
    built_khz = dut.registers.register_read(REG_CLOCK)
    if abs(built_khz - round(sync * 1000)) > 1:
        result["verdict"] = "clock mismatch"
        result["detail"] = f"gateware says {built_khz} kHz, host expects " \
                           f"{round(sync * 1000)} kHz"
        emit(handle, f"  {tag}: {result['detail']} -- refusing to measure")
        return result

    config = dut.registers.register_read(REG_CONFIG)
    bytes_per_word = (config >> 8) & 0xFF
    burst = (config >> 16) & 0xFFFF
    result["bytes_per_word"] = bytes_per_word
    result["burst_words"] = burst

    die_before = dut.registers.register_read(REG_DIE)
    window = poll(dut, target_words, handle, sync * 1e6, bytes_per_word)
    die_after = dut.registers.register_read(REG_DIE)
    status = dut.registers.register_read(REG_STATUS)

    result.update(window)
    result["die_before"] = die_before & 0x3F if die_before & DIE_PRESENT else None
    result["die_after"] = die_after & 0x3F if die_after & DIE_PRESENT else None
    result["dll_locked"] = bool(status & 1)
    result["dll_ready"] = bool(status & 2)
    result["burstdet"] = bool(status & 4)
    result["passes"] = (status >> 16) & 0xFFFF

    # Theoretical: eight data lines, DDR, at the part's own clock -- two bytes
    # per CK regardless of which PHY put the clock there.
    theoretical = ck * 1e6 * 2 / 1e6
    result["theoretical_mbps"] = theoretical
    result["read_pct"] = 100.0 * result["read_mbps"] / theoretical
    result["write_pct"] = 100.0 * result["write_mbps"] / theoretical

    if status & 8:      # a mismatch was recorded; keep how it was wrong
        result["first_bad"] = {
            "index": dut.registers.register_read(REG_BAD_INDEX),
            "got": dut.registers.register_read(REG_BAD_GOT),
            "want": dut.registers.register_read(REG_BAD_WANT),
        }
        capture = []
        for addr in range(8):
            dut.registers.register_write(REG_CAPTURE_ADDR, addr)
            capture.append(dut.registers.register_read(REG_CAPTURE_DATA))
        result["capture"] = capture

    if window["stalled"]:
        result["verdict"] = "stalled"
    elif window["errors"] == 0:
        result["verdict"] = "pass"
    elif window["errors"] >= window["words"] * 0.5:
        result["verdict"] = "fail (bulk)"
    else:
        result["verdict"] = "fail (intermittent)"

    rate = (window["errors"] / window["words"]) if window["words"] else 0
    result["error_rate"] = rate

    emit(handle,
         f"  CK {ck:>5.1f} {'DQS' if dqs else 'SDR':>3} sync {sync:>6.1f}  "
         f"read {result['read_mbps']:>6.1f} MB/s ({result['read_pct']:>4.1f}%)  "
         f"write {result['write_mbps']:>6.1f}  "
         f"words {window['words']:>11,}  errors {window['errors']:>11,}  "
         f"{'BD' if result['burstdet'] else '--'}  "
         f"die {result['die_before']}->{result['die_after']}  "
         f"{result['verdict']}")
    if "first_bad" in result:
        bad = result["first_bad"]
        emit(handle, f"        first mismatch: index {bad['index'] & 0xFFFF} "
                     f"pass {bad['index'] >> 16}, got {bad['got']:#010x}, "
                     f"wanted {bad['want']:#010x}")
    return result


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true", help="build only")
    parser.add_argument("--run", action="store_true", help="run only")
    parser.add_argument("--ck", type=float, nargs="+", default=DEFAULT_CK,
                        help="device CK rungs, MHz")
    parser.add_argument("--paths", nargs="+", default=["sdr", "dqs"],
                        choices=["sdr", "dqs"])
    parser.add_argument("--words", type=int, default=DEFAULT_WORDS,
                        help="words per rung before a verdict")
    parser.add_argument("--jobs", type=int, default=8,
                        help="parallel builds; the board is not involved")
    args = parser.parse_args()

    do_build = args.build or not args.run
    do_run = args.run or not args.build

    paths = [p == "dqs" for p in args.paths]
    ladder = rungs(args.ck, paths)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)

    with LOG.open("a") as handle:
        emit(handle, f"\n=== hyperram ceiling, "
                     f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} ===")
        emit(handle, f"{len(ladder)} rungs, {BURST_WORDS} words per burst "
                     f"(tCSM 4 us caps it), {args.words:,} words per verdict")

        if do_build:
            emit(handle, f"\nbuilding {len(ladder)} bitstreams, {args.jobs} at "
                         f"a time -- the board is not involved")
            built = {}
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                futures = {pool.submit(build_one, ck, dqs, sync, handle):
                           (ck, dqs, sync) for ck, dqs, sync in ladder}
                for future, (ck, dqs, sync) in futures.items():
                    ok, out, _ = future.result()
                    built[(ck, dqs)] = ok
                    tim = timing_of(out)
                    note = ""
                    if tim:
                        note = (f"{'MET' if tim[0] else 'FAIL'} "
                                f"{tim[1]:.1f}/{tim[2]:.1f} MHz on {tim[3]}")
                    emit(handle, f"  {tag_of(ck, dqs):>12}  sync {sync:>6.1f}  "
                                 f"{'built' if ok else 'BUILD FAILED':<12} {note}")

        results = []
        if do_run:
            emit(handle, "\nrunning, ascending CK -- each rung is configured "
                         "into SRAM, which a power cycle undoes")
            for ck, dqs, sync in sorted(ladder, key=lambda r: (r[1], r[0])):
                results.append(run_one(ck, dqs, sync, args.words, handle))

            RESULTS.write_text(json.dumps(results, indent=2))
            emit(handle, f"\nresults: {RESULTS}")

            for dqs in sorted(set(d for _, d, _ in ladder)):
                good = [r for r in results
                        if r["dqs"] == dqs and r["verdict"] == "pass"]
                name = "DQS" if dqs else "non-DQS"
                if good:
                    best = max(good, key=lambda r: r["ck_mhz"])
                    emit(handle, f"{name}: highest clean CK "
                                 f"{best['ck_mhz']:g} MHz, "
                                 f"{best['read_mbps']:.1f} MB/s read "
                                 f"({best['read_pct']:.1f}% of theoretical)")
                else:
                    emit(handle, f"{name}: no rung verified clean")

        emit(handle, f"log: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
