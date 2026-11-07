#!/usr/bin/env python3
#
# Mutation proof for the device-engine sims: each mutant is a one-line break of gateware/usbcore; the
# named sim sections must FAIL on it. #612.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
    python3 scripts/soc/usb_device_mutants.py            # every mutant
    python3 scripts/soc/usb_device_mutants.py toggle_inverted nyet_off

- Copies `gateware/usbcore` to `tmp/usb-device-mutants/<name>/usbcore`, applies one exact-text
  replacement (must match once), runs the sections with `USB_DEVICE_GATEWARE` pointing at the copy.
- KILLED = the sim reported failed checks. SURVIVED = it passed (a gap in the checks). ERROR = the
  sim crashed or the replacement did not apply (not a proof).
- Log: tmp/logs/usb_device_mutants.log; per-mutant output beside the copy.
"""

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from devlog import emit  # noqa: E402

OUT = ROOT / "tmp" / "usb-device-mutants"
RUN_LIMIT_S = 900       # backstop for the slowest section ('large', measured ~4 min under load 30) x ~3.5
WORKERS = 8

EP0, BULK = "usb_device_ep0_sim.py", "usb_device_bulk_sim.py"
D, B = "device.py", "bulk.py"

# name: (file, old, new, (script, sections...))
MUTANTS = {
    "toggle_inverted": (D, "with m.Elif(got_data1 != tog_o[k]):", "with m.Elif(got_data1 == tog_o[k]):", (BULK, "out")),
    "nyet_off": (D, "nyet = is_hs & (f.w_count + 1 >= self.fifo_slots)", "nyet = Signal()", (BULK, "backpressure")),
    "in_toggle_stuck": (D, "m.d.usb += tog_i[k].eq(~tog_i[k])", "m.d.usb += tog_i[k].eq(tog_i[k])", (BULK, "in")),
    "in_pop_on_timeout": (D, "with m.If(hs_det.detected.ack):\n                m.d.usb += b_wait.eq(0)\n",
                          "with m.If(hs_det.detected.ack | (b_ack_count >= self.ACK_WAIT_CYCLES)):\n                m.d.usb += b_wait.eq(0)\n", (BULK, "in")),
    "out_halt_ignored": (D, "with m.If(halt_o[k]):\n                            m.d.usb += resp.eq(3)", "with m.If(0):\n                            m.d.usb += resp.eq(3)", (BULK, "halt")),
    "clear_keeps_toggle": (D, "m.d.usb += [ha[k].eq(0), tg[k].eq(0)]", "m.d.usb += [ha[k].eq(0)]", (BULK, "halt")),
    "enable_keeps_fifo": (D, "tg[k].eq(0), fl[k].eq(1),\n", "tg[k].eq(0), fl[k].eq(0),\n", (BULK, "control")),
    "ping_always_ack": (D, "with m.Elif(ofifo[k].w_free):\n                        m.d.comb += hs_tx.issue_ack.eq(1)",
                        "with m.Elif(1):\n                        m.d.comb += hs_tx.issue_ack.eq(1)", (BULK, "ping")),
    "oversize_accepted": (D, "(rx_idx > cur_maxp) | ~data_pid_ok", "~data_pid_ok", (BULK, "out")),
    "dup_nak_not_ack": (D, "m.d.usb += resp.eq(1)                        # duplicate: ACK, drop",
                        "m.d.usb += resp.eq(2)", (BULK, "out")),
    "auto_zlp_off": (B, "owe_zlp.eq(s.last & full & self.auto_zlp)", "owe_zlp.eq(0)", (BULK, "in_zlp")),
    "no_split_at_maxp": (B, "full = nxt >= self.maxp", "full = nxt >= 1000", (BULK, "large")),
    "producer_ready_stuck": (B, "m.d.comb += s.ready.eq((f.w_free | (idx != 0)) & ~owe_zlp)",
                             "m.d.comb += s.ready.eq(~owe_zlp)", (BULK, "backpressure")),
    "fifo_never_full": (B, "self.w_free.eq(count != slots)", "self.w_free.eq(1)", (BULK, "backpressure")),
    "in_pid_always_data0": (D, "data_tx.data_pid.eq(Mux(b_sending, b_tog,", "data_tx.data_pid.eq(Mux(b_sending, 0,", (BULK, "in")),
    "flush_not_on_reset": (D, "fl_o.eq(0xF), fl_i.eq(0xF)", "fl_o.eq(0), fl_i.eq(0)", (BULK, "control")),
    "stale_not_checked": (D, "with m.If(self.cmd_seq != self.seq):", "with m.If(0):", (EP0, "stale"), (BULK, "control")),
    "uframe_stuck": (D, "Mux(self.uframe == 7, 7, self.uframe + 1)", "self.uframe", (EP0, "sof")),
    "suspend_late": (D, "with m.If(idle_count >= self.SUSPEND_CYCLES):", "with m.If(idle_count >= self.SUSPEND_CYCLES * 2):", (EP0, "suspend")),
    "wake_short": (D, "with m.If(timer_count >= self.RESUME_K_CYCLES):", "with m.If(timer_count >= self.RESUME_K_CYCLES // 2):", (EP0, "suspend")),
    "resume_stays_fs": (D, 'with m.If(was_hs):\n                            m.next = "HS"', 'with m.If(0):\n                            m.next = "HS"', (EP0, "suspend_hs")),
    "probe_never_resets": (D, "with m.If(u.line_state == 0):\n                        start_reset()", "with m.If(0):\n                        start_reset()", (EP0, "suspend_hs")),
    "detached_drives": (D, "m.d.comb += [u.term_select.eq(0), u.op_mode.eq(1)]", "m.d.comb += [u.term_select.eq(0), u.op_mode.eq(0)]", (EP0, "vbus")),
    "no_setup_event": (D, "m.d.comb += ev_set[3].eq(1)", "pass", (EP0, "events")),
    "irq_ignores_mask": (D, "self.irq.eq((self.events & self.ev_mask) != 0)", "self.irq.eq(self.events != 0)", (EP0, "events")),
    "no_fifo_event": (D, "m.d.comb += ev_set[6].eq(1)", "pass", (EP0, "events")),
}


def run_one(name):
    spec = MUTANTS[name]
    fname, old, new = spec[:3]
    runs = spec[3:]
    work = OUT / name
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(ROOT / "gateware" / "usbcore", work / "usbcore", ignore=shutil.ignore_patterns("__pycache__"))
    target = work / "usbcore" / fname
    text = target.read_text()
    if text.count(old) != 1:
        return name, "ERROR", f"replacement matches {text.count(old)} times"
    target.write_text(text.replace(old, new))
    env = dict(os.environ, USB_DEVICE_GATEWARE=str(work))
    verdict, detail = "SURVIVED", ""
    for script, *sections in runs:
        try:
            proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "soc" / script), *sections], env=env,
                                  capture_output=True, text=True, timeout=RUN_LIMIT_S)
        except subprocess.TimeoutExpired:
            return name, "ERROR", f"{script} {sections} exceeded {RUN_LIMIT_S} s"
        (work / f"{script}.{'_'.join(sections)}.log").write_text(proc.stdout + proc.stderr)
        if "Traceback" in proc.stderr:
            return name, "ERROR", f"{script} {sections} crashed: {proc.stderr.strip().splitlines()[-1]}"
        failed = [line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("FAIL")]
        if proc.returncode != 0 or failed:
            verdict, detail = "KILLED", f"{script} {' '.join(sections)}: {len(failed)} checks failed, e.g. {failed[0][6:] if failed else '-'}"
            break
    return name, verdict, detail


def main(argv):
    names = [a for a in argv if not a.startswith("-")] or list(MUTANTS)
    unknown = [n for n in names if n not in MUTANTS]
    if unknown:
        raise SystemExit(f"unknown mutants {unknown}; known: {list(MUTANTS)}")
    OUT.mkdir(parents=True, exist_ok=True)
    emit(f"device-engine mutation proof: {len(names)} mutants")
    bad = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        for name, verdict, detail in pool.map(run_one, names):
            emit(f"  {verdict:9} {name}: {detail}")
            bad += verdict != "KILLED"
    emit(f"  {len(names) - bad}/{len(names)} killed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
