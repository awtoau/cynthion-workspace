#!/usr/bin/env python3
# AOA one-way bulk streams, closed loop: correctness at large sizes, not speed.
# SPDX-License-Identifier: BSD-3-Clause
"""
    ./scripts/soc/aoa_stream.py [--sizes 65536 262144 1048576] [--phone IP]

Per size and direction, from a fresh attach (aoa_repeat.attach):
- source: the app sends `count` AOAT frames of 16384 B (`size` counts the 24 B header); the Cynthion
  `pump in` reads exactly that many bytes. Passes when the Cynthion byte count, the app's frame/byte
  counts and write_errors=0 agree and the app logged no error.
- sink: the Cynthion `pump out`s the ramp with the app in sink mode. Passes when the pump completes,
  the app logged no error, the accessory is still open and the app accounts for every byte
  (resync_bytes; the sink parses AOAT, so the ramp is counted, not verified; echo is the content check).
Stops at the first failure. Log: tmp/logs/aoa_stream.log. Needs `vbus control` and the app (aoa_repeat).
"""

import argparse
import json
import re
import sys
import time

import aoa_repeat as base

LOG = base.ROOT / "tmp" / "logs" / "aoa_stream.log"
FRAME = 16384           # on-wire frame: the app's read size, see aoa_repeat
# App result lands within one poll after the last frame; 3 s = 3x its 1 s write floor.
RESULT_WAIT_S = 3.0
# soc_shell returns 30 ms after the prompt redraw, before a slow command prints; a short second
# command makes the capture wait for the first one's result line.
FLUSH = "usb target_a role"


def pump_result(text, direction):
    """(bytes, stopped) from a `pump` line; bytes is None when no result line was printed."""
    done = re.search(rf"pump {direction} out (\d+) in (\d+) bytes", text)
    stopped = re.search(r"stopped: .*", text)
    got = None if not done else int(done.group(2 if direction == "in" else 1))
    return got, stopped.group(0) if stopped else None


def app_result(phone, log, want_frames):
    """`ctl get` until the app counts `want_frames` frames, or RESULT_WAIT_S passes."""
    deadline = time.monotonic() + RESULT_WAIT_S
    while True:
        got = base.reply(base.ctl(phone, "get"))
        result = got.get("result") or {}
        if (result.get("frames") or 0) >= want_frames or time.monotonic() > deadline:
            log.write("app result: " + json.dumps(result) + "\n")
            return got, result
        time.sleep(base.POLL_S)


def stream_source(total, args, events, log):
    count = total // FRAME
    mode = base.reply(base.ctl(args.phone, "mode", "source", f"test={count}", f"size={FRAME}",
                               f"count={count}", "pattern=ramp", "rate=0"))
    log.write(json.dumps(mode) + "\n")
    if not mode.get("ok"):
        return False, f"app refused source mode: {mode}"
    quiet = events.mark()
    text = base.shell([f"usb target_a pump in 1 {total}", FLUSH])
    log.write(text)
    got, stopped = pump_result(text, "in")
    state, result = app_result(args.phone, log, count)
    errors = [t for t in events.since(quiet) if base.APP_ERROR.search(t)]
    if errors:
        return False, f"app error during source: {errors[0][:160]}"
    if stopped or got != total:
        return False, f"Cynthion read {got} of {total} B ({stopped}); app sent {result.get('frames')} frames"
    if result.get("frames") != count or result.get("write_errors") or result.get("pass") is not True:
        return False, f"app source result disagrees: {result}"
    return True, f"{total} B in {count} frames"


def stream_sink(total, args, events, log):
    mode = base.reply(base.ctl(args.phone, "mode", "sink", f"test={total // FRAME}"))
    log.write(json.dumps(mode) + "\n")
    if not mode.get("ok"):
        return False, f"app refused sink mode: {mode}"
    quiet = events.mark()
    text = base.shell([f"usb target_a pump out 1 {total} 1", FLUSH])
    log.write(text)
    got, stopped = pump_result(text, "out")
    state, result = app_result(args.phone, log, 0)
    errors = [t for t in events.since(quiet) if base.APP_ERROR.search(t)]
    if errors:
        return False, f"app error during sink: {errors[0][:160]}"
    if stopped or got != total:
        return False, f"Cynthion wrote {got} of {total} B ({stopped}); app result {result}"
    if not state.get("accessory_open"):
        return False, "app reports the accessory closed after sink"
    # The ramp is not AOAT, so the sink skips it as resync bytes; up to 3 may sit in the checker
    # as a possible start of the 4-byte frame magic.
    held = total - (result.get("resync_bytes") or 0)
    if not 0 <= held <= 3:
        return False, f"app accounted {result.get('resync_bytes')} of {total} B written: {result}"
    return True, f"{total} B written, {total - held} B accounted by the app"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sizes", type=int, nargs="+", default=[65536, 262144, 1048576])
    parser.add_argument("--phone", default="192.168.25.125")
    parser.add_argument("--app-log", default=str(base.DEFAULT_APP_LOG))
    parser.add_argument("--only", choices=["source", "sink"])
    args = parser.parse_args()
    if any(size % FRAME for size in args.sizes):
        raise SystemExit(f"sizes must be multiples of {FRAME} (whole AOAT frames)")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    events = base.AppEvents(args.app_log)
    stages = [(name, fn) for name, fn in (("source", stream_source), ("sink", stream_sink))
              if args.only in (None, name)]
    try:
        with LOG.open("w") as log:
            for name, fn in stages:
                for total in args.sizes:
                    started = time.monotonic()
                    mark = events.mark()
                    ok, why = base.attach(args, events, log)
                    if ok:
                        ok, why = fn(total, args, events, log)
                    log.write("app events:\n" + "\n".join("  " + t for t in events.since(mark)) + "\n")
                    base.say(f"{name} {total}: {'PASS' if ok else 'FAIL'}  {why}  "
                             f"{time.monotonic() - started:.0f} s", log)
                    if not ok:
                        base.say(f"stopped at the first failure; details in {LOG.relative_to(base.ROOT)}", log)
                        return 1
    finally:
        events.close()
    print("all streams passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
