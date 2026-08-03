#!/usr/bin/env python3
#
# Load the fabric test into SRAM and watch it. See awtoau/pluribus#98.
# SPDX-License-Identifier: BSD-3-Clause

"""
Loads `ecp5-test/fabric` into the FPGA's volatile configuration and monitors it.

**SRAM only. This never writes flash.** Configuration loaded this way is undone
by a power cycle, so the board returns to its own boot gateware without any
recovery step. Writing flash would risk that gateware for no gain: the question
is whether the fabric computes correctly while running, and volatile
configuration answers it exactly as well.

What is being watched, and why each part is needed
--------------------------------------------------

The design advances 185 blocks every cycle and, every 2**18 cycles, latches the
XOR of all their states and compares it against a constant baked in at build
time. So the *gateware* is the thing doing the checking, continuously, whether
or not this script is looking. What this script adds:

  * an independent host-side check of that constant, recomputed from the
    specification here rather than trusted from the build. If the gateware were
    built against a wrong golden value it would report clean forever, and that
    is the single failure mode that turns this experiment into a false pass.

  * a running record of the round counter, so "it ran" is a number of verified
    rounds rather than an impression.

  * the sticky mismatch flag and the mismatch count, which the gateware latches
    and never clears.

The mismatch count matters more than the flag. Case 2 of the three explanations
in #98 -- that 12F parts are salvage which failed test in the extra region --
predicts intermittent errors, so the interesting quantity is a rate, and a rate
needs a count and a duration.

Timing
------

Nothing here sleeps or waits on a duration. The loop polls JTAG as fast as the
link allows and stops on whichever of `--rounds` or `--polls` is reached first,
both of which are counts. The round counter advancing between polls is what
proves the fabric is running; wall-clock time is recorded only so a mismatch
rate can be reported per second.

Die temperature
---------------

REG_DIE is read before and after the polling loop. The ECP5's DTR reports an
uncalibrated code rather than degrees, so the code is what is recorded -- but it
turns "one operating point" from a caveat into a number. Across a sweep of
placements it says whether they all ran at the same temperature, and if a soak
ever shows mismatches correlating with it, that is the signature of a marginal
part rather than a hard defect. This test cannot currently tell those apart.

    ./scripts/fabric_run.py                    # load, then watch
    ./scripts/fabric_run.py --no-load          # watch what is already loaded
    ./scripts/fabric_run.py --rounds 2000000   # a longer soak
    ./scripts/fabric_run.py --bitstream tmp/fabric-coverage/seed-003/top.bit
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "fabric_run.log"
BITSTREAM = ROOT / "tmp" / "fabric" / "build" / "top.bit"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fabric.fabric_gateware import (APPLET_ID, BLOCKS, DIE_PRESENT, ROUND_BITS,
                                    REG_DIE, REG_ID,
                                    REG_SIGNATURE, REG_ROUNDS, REG_STATUS,
                                    REG_GOLDEN, REG_MISMATCHES, SYNC_MHZ)

# The volatile configuration path. `configure` writes SRAM; `flash` would write
# the board's boot gateware, which this script must never do.
CONFIGURE = [sys.executable,
             str(ROOT / "repos" / "apollo" / "apollo_fpga" / "commands"
                 / "cli.py"),
             "configure"]


def die_note(value):
    """Describe REG_DIE, without pretending an uncalibrated code is degrees.

    FPGA-TN-02210 Table 4.3 maps the 6-bit DTR code to a temperature band. The
    mapping is documented as uncalibrated and part-dependent, so the code is
    what gets recorded and compared; what matters for this test is whether the
    operating point moved between configurations, and the code answers that
    without a conversion that would imply an accuracy nobody measured.
    """
    if not value & DIE_PRESENT:
        return "no DTR in this bitstream"
    return f"code {value & 0x3F} (raw DTROUT {value & 0xFF:#04x})"


def load(bitstream, emit):
    """Configure the FPGA over JTAG, into SRAM. Returns True on success."""
    if not bitstream.exists():
        emit(f"no bitstream at {bitstream} -- run scripts/fabric_build.py")
        return False
    emit(f"loading {bitstream} into SRAM (volatile; a power cycle undoes it)")
    result = subprocess.run(CONFIGURE + [str(bitstream)], cwd=ROOT,
                            capture_output=True, text=True)
    for line in ((result.stdout or "") + (result.stderr or "")).splitlines():
        emit(f"  {line}")
    if result.returncode != 0:
        emit("configure failed")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-load", action="store_true",
                        help="do not reconfigure; watch what is already running")
    parser.add_argument("--rounds", type=int, default=100_000,
                        help="stop once the design reports this many rounds")
    parser.add_argument("--blocks", type=int, default=BLOCKS,
                        help="block count used to build this bitstream")
    parser.add_argument("--round-bits", type=int, default=ROUND_BITS,
                        help="round size used to build this bitstream")
    parser.add_argument("--topology-seed", type=lambda value: int(value, 0),
                        default=0,
                        help="signature topology used to build this bitstream")
    parser.add_argument("--polls", type=int, default=2_000_000,
                        help="hard cap on JTAG polls, so the loop cannot run "
                             "forever if the round counter is stuck")
    parser.add_argument("--report-every", type=int, default=500,
                        help="polls between progress lines")
    parser.add_argument("--bitstream", type=Path, default=BITSTREAM,
                        help="which bitstream to load; a sweep builds one per "
                             "placement and each lives in its own directory")
    parser.add_argument("--result-json", type=Path, default=None,
                        help="write the verdict here as well, so a driver "
                             "reads numbers rather than re-parsing this "
                             "transcript")
    parser.add_argument("--log", type=Path, default=LOG,
                        help="where this run's transcript is appended")
    args = parser.parse_args()
    round_cycles = 1 << args.round_bits

    result = {"bitstream": str(args.bitstream), "verdict": "incomplete",
              "requested_blocks": args.blocks,
              "round_bits": args.round_bits,
              "topology_seed": args.topology_seed}

    def finish(code):
        if args.result_json:
            args.result_json.parent.mkdir(parents=True, exist_ok=True)
            args.result_json.write_text(json.dumps(result, indent=2))
        return code

    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("a") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

        started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        emit(f"=== fabric test run, {started_at} ===")
        result["started"] = started_at

        # Recompute the golden value here, from the specification, rather than
        # reading it out of the build log. A gateware built against a wrong
        # constant would report clean forever; this is the check that catches it.
        from fabric_golden import golden as compute_golden, verify_vector_model
        checked = verify_vector_model(args.blocks, 300)
        emit(f"golden model cross-checked against the scalar specification "
             f"over {checked} cycles, {args.blocks} blocks")
        expected = compute_golden(args.blocks, round_cycles,
                                  args.topology_seed)
        emit(f"golden signature, computed on the host: {expected:#010x}")

        result["expected_golden"] = f"{expected:#010x}"

        if not args.no_load:
            if not load(args.bitstream, emit):
                result["verdict"] = "load failed"
                return finish(1)

        from apollo_fpga import ApolloDebugger
        dut = ApolloDebugger()
        emit(f"apollo firmware: {dut.get_firmware_version()}")

        applet = dut.registers.register_read(REG_ID)
        if applet != APPLET_ID:
            emit(f"wrong bitstream: REG_ID {applet:#010x}, expected "
                 f"{APPLET_ID:#010x} -- refusing to report a result for "
                 f"gateware that is not this test")
            result["verdict"] = "wrong bitstream"
            return finish(1)
        emit(f"REG_ID {applet:#010x} -- the fabric test is running")

        status = dut.registers.register_read(REG_STATUS)
        blocks = (status >> 8) & 0xFF
        clock = (status >> 16) & 0xFFFF
        built_golden = dut.registers.register_read(REG_GOLDEN)
        emit(f"gateware reports {blocks} blocks at {clock} MHz")
        emit(f"gateware's own golden constant: {built_golden:#010x}")

        # Read before the polling loop and again after it, so the pair says
        # whether the part warmed up while the design ran rather than only what
        # it read at one instant.
        die_before = dut.registers.register_read(REG_DIE)
        emit(f"die temperature at start: {die_note(die_before)}")
        result.update(blocks=blocks, sync_mhz=clock,
                      built_golden=f"{built_golden:#010x}",
                      die_before=die_before)

        if built_golden != expected:
            emit(f"REFUSING: the gateware checks itself against "
                 f"{built_golden:#010x} but the host computes "
                 f"{expected:#010x}.")
            emit("  A clean result from this bitstream would be meaningless, "
                 "because the constant it compares against is not the right "
                 "answer. Rebuild.")
            result["verdict"] = "golden mismatch between gateware and host"
            return finish(1)
        emit("the gateware's constant and the host's model agree")
        if blocks != args.blocks:
            emit(f"note: gateware built for {blocks} blocks, this script's "
                 f"argument says {args.blocks}")

        emit()
        emit(f"round = 2**{args.round_bits} = {round_cycles} cycles of all {blocks} "
             f"blocks; {round_cycles * blocks * 32:,} state bits advanced per "
             f"round")
        emit("polling; nothing here waits on a duration -- the loop stops on "
             f"{args.rounds} rounds or {args.polls} polls, whichever comes "
             "first")
        emit()

        start = time.perf_counter()
        first_rounds = dut.registers.register_read(REG_ROUNDS)
        polls = 0
        signature_reads = 0
        signature_bad = 0
        worst_mismatches = 0
        ever_mismatch = False
        rounds = first_rounds

        while polls < args.polls:
            polls += 1
            rounds = dut.registers.register_read(REG_ROUNDS)
            mismatches = dut.registers.register_read(REG_MISMATCHES)
            status = dut.registers.register_read(REG_STATUS)
            signature = dut.registers.register_read(REG_SIGNATURE)

            signature_reads += 1
            if signature != expected:
                signature_bad += 1
                emit(f"  poll {polls}: signature {signature:#010x} != "
                     f"{expected:#010x} at round {rounds}")

            if mismatches > worst_mismatches:
                worst_mismatches = mismatches
            if status & (1 << 2):
                ever_mismatch = True

            if polls % args.report_every == 0:
                elapsed = time.perf_counter() - start
                done = rounds - first_rounds
                emit(f"  {elapsed:8.1f}s  polls {polls:>7}  rounds "
                     f"{done:>10}  sticky {'SET' if status & (1 << 2) else 'clear'}  "
                     f"mismatched rounds {mismatches}")

            if rounds - first_rounds >= args.rounds:
                break

        elapsed = time.perf_counter() - start
        done = rounds - first_rounds
        die_after = dut.registers.register_read(REG_DIE)

        emit()
        emit(f"=== result, {time.strftime('%Y-%m-%dT%H:%M:%S%z')} ===")
        emit(f"ran {elapsed:.1f}s, {polls} JTAG polls")
        emit(f"die temperature at end: {die_note(die_after)}"
             + (f", from {die_note(die_before)} at the start"
                if die_after != die_before else " (unchanged)"))
        emit(f"rounds completed by the gateware: {done:,}")
        emit(f"  each round is {round_cycles} cycles of {blocks} blocks, so "
             f"{done * round_cycles:,} block-cycles, "
             f"{done * round_cycles * blocks * 32:,} state-bit updates")
        emit(f"gateware-checked rounds that mismatched: {worst_mismatches}")
        emit(f"sticky mismatch flag: {'SET' if ever_mismatch else 'never set'}")
        emit(f"host signature reads: {signature_reads}, "
             f"disagreeing with the model: {signature_bad}")

        result.update(seconds=round(elapsed, 1), polls=polls, rounds=done,
                      mismatches=worst_mismatches, sticky=ever_mismatch,
                      signature_reads=signature_reads,
                      signature_bad=signature_bad, die_after=die_after)

        if done == 0:
            emit()
            emit("INCONCLUSIVE -- the round counter never advanced. The design "
                 "is loaded but not running, so nothing was exercised.")
            result["verdict"] = "inconclusive"
            return finish(1)

        if ever_mismatch or worst_mismatches or signature_bad:
            emit()
            emit("MISMATCH OBSERVED -- this part computed a wrong signature "
                 "while running a design that occupies fabric beyond the "
                 "12,288 LUTs it advertises.")
            emit("  That is consistent with the salvage explanation, but it is "
                 "not proof of it: a timing marginality or a supply problem "
                 "would look the same. Rerun, and rerun a build that fits "
                 "inside 12,288 LUTs for comparison.")
            result["verdict"] = "mismatch"
            return finish(1)

        emit()
        emit(f"PASS for this part at this moment: {done:,} rounds, every one "
             f"checked by the gateware against {expected:#010x}, no mismatch "
             f"latched.")
        emit("  Establishes: a design occupying fabric well beyond the 12,288 "
             "LUTs this part advertises placed, closed timing and computed the "
             "correct signature. The extra fabric is not plainly dead and not "
             "plainly unclocked here.")
        emit("  Does NOT establish: that intermittent per-part defects are "
             "absent. The salvage explanation predicts occasional wrongness, "
             "and a run of this length cannot measure a rate -- so it remains "
             "compatible with what was just observed.")
        emit("  Nor anything about other parts, boards, temperatures or "
             "supplies. One sample, one moment.")
        emit(f"log: {args.log}")
        result["verdict"] = "pass"

    return finish(0)


if __name__ == "__main__":
    sys.exit(main())
