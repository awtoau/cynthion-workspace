#!/usr/bin/env python3
#
# Closed-loop board test of the #588 HyperRAM peripheral, through the shell.
# SPDX-License-Identifier: BSD-3-Clause

"""Drives `hr` on a running SoC and judges both ends: the command's code and the counters.

- status (ready, calibrated, CK measured), calibrate, register read
- TEST write / read / write+read over the whole 8 MiB, zero errors and missing
- bytes: write at odd and even addresses, read back, neighbours untouched
- forced errors: `hr inject hang` must report the watchdog, `hr inject xor` every word
- ends with `hr errors` clear and a table on stdout + tmp/logs/hyperram_board_test.log

    ./scripts/soc/hyperram_board_test.py
"""

from __future__ import annotations

import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "tmp" / "logs" / "hyperram_board_test.log"
WHOLE = 0x80_0000


def shell(*commands: str) -> str:
    """Bounded by soc_shell's per-command budget; failure is a FAIL line, not a hang."""
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "soc_shell.py"), *commands],
                       capture_output=True, text=True)
    return r.stdout + r.stderr


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = LOG.open("w")
    results = []

    def out(line=""):
        print(line, flush=True)
        log.write(line + "\n")

    def check(what, ok, detail=""):
        results.append((what, ok))
        out(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"\n        {detail}" if not ok else ""))
        return ok

    out(f"hyperram board test {datetime.datetime.now().astimezone().isoformat(timespec='seconds')}")
    text = shell("hr errors", "hr status")
    out(text)
    m = re.search(r"ck (\d+) kHz requested, (\d+) kHz measured", text)
    check("status: ready, locked, calibrated",
          "ready 1 locked 1 calibrated 1" in text, text[-400:])
    check("CK measured within 0.1% of requested",
          bool(m) and abs(int(m.group(1)) - int(m.group(2))) * 1000 <= int(m.group(1)),
          m.group(0) if m else "no CK line")

    text = shell("hr calibrate")
    out(text)
    cal = re.search(r"cal tap (\d+) width (\d+) valid (\d)", text)
    check("calibrate: ok, a clean window", "calibrate ok" in text and bool(cal)
          and cal.group(3) == "1" and int(cal.group(2)) > 0, text[-300:])

    text = shell("hr reg cr0")
    out(text)
    check("CR0 reads 8f2f after calibration", "reg ok" in text and "value 8f2f" in text, text)

    rows = []
    for mode in ("w", "r", "wr"):
        text = shell(f"hr test {mode} {WHOLE:x} 0 1a2b")
        out(text)
        ok = "test ok" in text and re.search(r"errors 0 missing 0", text) is not None
        if mode == "w":
            ok = "test ok" in text
        rate = re.search(r"= (\d+\.\d) MB/s", text)
        checked = re.search(r"checked (\d+)", text)
        rows.append((mode, rate.group(1) if rate else "?", checked.group(1) if checked else "?"))
        check(f"test {mode} over 8 MiB: ok" + ("" if mode == "w" else ", 0 errors, 0 missing"),
              ok, text[-400:])
    out("\n| mode | MB/s | words checked |\n|---|---|---|")
    for row in rows:
        out(f"| {row[0]} | {row[1]} | {row[2]} |")

    for addr, data in ((0x123457, ["a5", "5a", "c3"]), (0x200000, ["01", "02", "03", "04"])):
        shell(f"hr fill {addr - 4:x} 10 const 11111111")
        text = shell(f"hr write {addr:x} {' '.join(data)}", f"hr read {addr - 2:x} 8")
        out(text)
        want = " ".join(["11", "11"] + data + ["11"] * (6 - len(data)))
        check(f"bytes at {addr:#x}: written, neighbours untouched", want in text,
              f"want '{want}'")

    text = shell("hr inject hang")
    out(text)
    check("forced hang: watchdog code and sticky wdog", "inject hang watchdog" in text
          and " wdog" in text, text[-300:])
    text = shell("hr status")
    check("recovered after the watchdog: ready and calibrated",
          "ready 1 locked 1 calibrated 1" in text, text[-300:])
    text = shell("hr inject xor")
    out(text)
    check("forced checker failure: every word fails", "test check" in text
          and "checked 2048 errors 2048" in text, text[-300:])
    text = shell("hr errors", "hr errors")
    out(text)
    check("errors clear after reading", text.rstrip().endswith("errors 0000") or
          "errors 0000\n" in text.split("hr errors")[-1], text[-200:])

    failed = [w for w, ok in results if not ok]
    out(f"\n{len(results) - len(failed)}/{len(results)} passed" +
        (f"; FAILED: {', '.join(failed)}" if failed else ""))
    out(f"log: {LOG}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
