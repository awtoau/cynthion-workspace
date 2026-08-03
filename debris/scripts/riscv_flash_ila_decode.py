#!/usr/bin/env python3
#
# Decode the flash ILA trace into a waveform and compare the two captures.
# SPDX-License-Identifier: BSD-3-Clause

"""
Turns the ILA's hex dump into something a person can read, and diffs the
working path against the broken one.

The firmware captures two windows with an identical ILA configuration: a
memory-mapped read, which is verified correct against `apollo flash-read`, and
a JEDEC read through the controller, which returns zeros. Both sample the same
`dq_i[1]` through the same PHY. So the question is not what either trace looks
like in isolation -- it is where they DIFFER, and specifically whether the
input capture strobe keeps the same relationship to the clock in both.

Reads the console, extracts both traces, and reports for each:

  - how many SCK rising edges occurred
  - how many capture strobes fired
  - the phase of each strobe relative to the clock, as a histogram of the
    strobe's position within the SCK period

That last one is the measurement. A strobe that consistently fires at the same
offset is sampling correctly; one that scatters, or fires at a different offset
in one trace than the other, is not.

    ./scripts/riscv_flash_ila_decode.py
    ./scripts/riscv_flash_ila_decode.py --from-log tmp/logs/ila_capture.log
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "riscv_flash_ila_decode.log"

sys.path.insert(0, str(ROOT / "ecp5-test"))

# Bit positions, matching FlashILA.SIGNAL_NAMES in ecp5-test/riscv/vexii_flash.py.
# This list IS the trace format; the gateware packs by position.
BITS = {
    "sck": 0, "dq_i1": 1, "cs": 2, "sr_in_shift": 3,
    "sample": 4, "update": 5, "in_xfer": 6, "dq_o0": 7,
}

# How long to wait for a line of console output, in seconds.
#
# Waiting for: one line of the hex dump. Why this value: the firmware prints the
# trace as fast as the USB console accepts it, so lines arrive in milliseconds;
# 5 s is far longer than that while still bounding a dead board. On expiry:
# report what arrived, because a partial trace is still diagnostic.
LINE_TIMEOUT_S = 5.0

# The dump is 1024 samples at 32 per line, plus headers. 200 lines covers both
# traces with margin.
MAX_LINES = 200


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def collect(handle):
    """Read the console until both traces have arrived."""
    import serial
    import usb_ids

    node = usb_ids.wait_for_tty("riscv_console", settles=1)
    if node is None:
        emit(handle, "no riscv_console tty; is the board configured?")
        return None
    emit(handle, f"console: {node}")

    lines = []
    with serial.Serial(node, 115200, timeout=LINE_TIMEOUT_S) as port:
        seen_traces = 0
        for _ in range(MAX_LINES):
            raw = port.readline()
            if not raw:
                break
            line = raw.decode("ascii", "replace").rstrip("\r\n")
            lines.append(line)
            if line.startswith("ila "):
                seen_traces += 1
            # Stop once the second trace has finished dumping.
            if seen_traces >= 2 and re.match(r"^  000003e0 ", line):
                break
    return lines


def parse(lines):
    """Split the console text into {label: [sample bytes]}."""
    traces, label, samples = {}, None, []
    for line in lines:
        header = re.match(r"^ila (.+)$", line)
        if header:
            if label is not None:
                traces[label] = samples
            label, samples = header.group(1), []
            continue
        body = re.match(r"^  ([0-9a-f]{8}) ([0-9a-f]+)$", line)
        if body and label is not None:
            hexdata = body.group(2)
            samples += [int(hexdata[i:i + 2], 16)
                        for i in range(0, len(hexdata) - 1, 2)]
    if label is not None:
        traces[label] = samples
    return traces


def analyse(handle, label, samples):
    """Report edges, strobes, and the strobe's phase relative to SCK."""
    emit(handle)
    emit(handle, f"--- {label}  ({len(samples)} samples)")
    if not samples:
        emit(handle, "    empty")
        return None

    def bit(sample, name):
        return (sample >> BITS[name]) & 1

    rising = []
    strobes = []
    for i in range(1, len(samples)):
        if bit(samples[i], "sck") and not bit(samples[i - 1], "sck"):
            rising.append(i)
        if bit(samples[i], "sr_in_shift"):
            strobes.append(i)

    selected = sum(1 for s in samples if bit(s, "cs"))
    emit(handle, f"    cs asserted for      {selected} samples")
    emit(handle, f"    SCK rising edges     {len(rising)}")
    emit(handle, f"    capture strobes      {len(strobes)}")

    if not rising:
        emit(handle, "    NO CLOCK in this window -- nothing to phase against")
        return {"rising": 0, "strobes": len(strobes), "phase": {}}

    # Phase: for each strobe, how many samples after the most recent rising
    # edge did it fire? A correct sampler is consistent; a broken one scatters.
    phase = {}
    for s in strobes:
        prior = [r for r in rising if r <= s]
        if prior:
            offset = s - prior[-1]
            phase[offset] = phase.get(offset, 0) + 1
    if phase:
        summary = ", ".join(f"+{k}: {v}" for k, v in sorted(phase.items()))
        emit(handle, f"    strobe phase vs SCK  {summary}")
    else:
        emit(handle, "    strobes never follow a rising edge")

    # Where the strobes sit across the window shows whether later transfers
    # still capture. Reported as which quarter of the active region each falls
    # in, which is enough to see "only the first transfer" without a waveform.
    if strobes and rising:
        span_lo, span_hi = rising[0], rising[-1]
        span = max(1, span_hi - span_lo)
        quarters = [0, 0, 0, 0]
        for s in strobes:
            q = min(3, max(0, int(4 * (s - span_lo) / span)))
            quarters[q] += 1
        emit(handle, f"    strobes by quarter   {quarters}")

    return {"rising": len(rising), "strobes": len(strobes), "phase": phase}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-log", type=Path,
                        help="decode a saved capture instead of reading the board")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        if args.from_log:
            lines = args.from_log.read_text().splitlines()
        else:
            lines = collect(handle)
            if lines is None:
                return 1

        traces = parse(lines)
        if not traces:
            emit(handle, "no ila traces found in the console output")
            for line in lines[:10]:
                emit(handle, f"    {line!r}")
            return 1

        results = {}
        for label, samples in traces.items():
            results[label] = analyse(handle, label, samples)

        # The comparison this exists for.
        emit(handle)
        control = next((k for k in results if "mmap" in k), None)
        test = next((k for k in results if "ctrl" in k), None)
        if control and test and results[control] and results[test]:
            c, x = results[control], results[test]
            if c["rising"] == 0:
                emit(handle, "POSITIVE CONTROL SHOWS NO CLOCK. The instrument "
                             "is wrong; ignore everything it says about the "
                             "controller until this is fixed.")
            elif c["phase"] == x["phase"]:
                emit(handle, "Strobe phase is IDENTICAL in both paths, so the "
                             "input is sampled the same way in each. The fault "
                             "is not the capture strobe's timing.")
            else:
                emit(handle, "STROBE PHASE DIFFERS between the working and "
                             "broken paths:")
                emit(handle, f"    {control}: {c['phase']}")
                emit(handle, f"    {test}: {x['phase']}")

        emit(handle)
        emit(handle, f"log: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
