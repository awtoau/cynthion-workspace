#!/usr/bin/env python3
#
# Simulate the HyperRAM sequential baseline against a datasheet model (#581). REFERENCE: see gateware/hyperram_baseline/README.md.
# SPDX-License-Identifier: BSD-3-Clause

"""`gateware/hyperram_baseline` (engine + controller) against a W956A8 model written here.

- Model: datasheet rev A01-006 only -- CA decode (Tables 1/2), fixed latency 2L with the
  count starting 1 CK before the CA ends, RWDS High over the CA, read bytes A/B edge
  aligned with RWDS (Fig 6), write mask on RWDS (Fig 7), CR0 default 0x8F2F.
- Pin path modelled as latencies: ODDR out `LP`, CS#/OE fabric `LCS`, device + board
  round trip `R` half-cycles (odd R exercises the straddled-word branch), IDDR `LI`.
- Checks: word counts, zero missing, device memory == pattern, checker clean, negative
  controls (xor, wrong latency) fail, CR0 read, CS# Low within tCSM.
- Says nothing about pad timing, the PLL phase or the part's AC limits.

    ./scripts/hyperram/hyperram_baseline_sim.py     # log: tmp/logs/hyperram_baseline_sim.log
"""

import collections
import logging
import os
import subprocess
import sys
from pathlib import Path


def repo_root():
    env = os.environ.get("CYNTHION_WORKSPACE_REPO")
    if env:
        return Path(env)
    try:
        return Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                                   text=True, check=True, cwd=Path(__file__).parent).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return Path(__file__).resolve().parent.parent.parent


ROOT = repo_root()
LOG = ROOT / "tmp" / "logs" / "hyperram_baseline_sim.log"
sys.path.insert(0, str(ROOT / "gateware" / "hyperram_baseline"))

from amaranth import DomainRenamer, Module                       # noqa: E402
from amaranth.hdl import Fragment                                # noqa: E402
from amaranth.sim import Simulator                               # noqa: E402

from hyperbus import HyperBusCore, T_CSM_NS, WAIT_DEFAULT, max_burst_words  # noqa: E402
from top import (MODE_BOTH, MODE_READ, MODE_REG, MODE_WRITE,     # noqa: E402
                 SeqEngine, pattern_py)

log = logging.getLogger("hyperram_baseline_sim")

CK_MHZ, SYNC_MHZ = 166.0, 100.0
LP, LCS, LI = 3, 1, 1
DEVICE_LATENCY = 7                    # CR0[7:4] default, fixed 2x
CR0_DEFAULT = 0x8F2F
TOTAL = 2048


class Device:
    """W956A8 at the 16-bit-per-CK level, from the datasheet."""

    def __init__(self):
        self.mem = {}
        self.state = "idle"
        self.ca = []
        self.cs_low = 0
        self.cs_low_max = 0

    def step(self, cs, ck_en, dq_o, rwds_o):
        """One CK cycle; returns the two half-cycle (dq, rwds) samples the device drives."""
        quiet = [(0, 0), (0, 0)]
        if not cs:
            self.state, self.ca, self.cs_low = "idle", [], 0
            return quiet
        self.cs_low += 1
        self.cs_low_max = max(self.cs_low_max, self.cs_low)
        if not ck_en:
            return quiet
        if self.state == "idle":
            self.state = "ca"
        if self.state == "ca":
            self.ca.append(dq_o)
            if len(self.ca) == 3:
                ca = (self.ca[0] << 32) | (self.ca[1] << 16) | self.ca[2]
                self.read = bool(ca >> 47 & 1)
                self.regsp = bool(ca >> 46 & 1)
                self.addr = ((ca >> 16) & ((1 << 29) - 1)) << 3 | (ca & 7)
                self.left = 2 * DEVICE_LATENCY - 1
                self.state = "lat"
            return [(0, 1), (0, 1)]
        if self.state == "lat":
            self.left -= 1
            if self.left == 0:
                self.state = "data"
            return quiet
        if self.read:
            word = (CR0_DEFAULT if self.addr == 0x800 else 0) if self.regsp \
                else self.mem.get(self.addr, 0x5A5A)
            self.addr += 1
            return [(word >> 8, 1), (word & 0xFF, 0)]
        if rwds_o == 0:
            self.mem[self.addr] = dq_o
        self.addr += 1
        return quiet


def simulate(cases, r_halves):
    core = HyperBusCore(ck_mhz=CK_MHZ)
    eng = SeqEngine(core=core)
    m = Module()
    m.submodules.core = DomainRenamer({"sync": "hr"})(core)
    m.submodules.eng = eng
    p = core.pins
    dev = Device()
    results = []

    async def board(ctx):
        out_q = collections.deque([(0, 0, 0)] * LP)
        cs_q = collections.deque([(0, 0, 0)] * LCS)
        ret_q = collections.deque([(0, 0)] * r_halves)
        in_q = collections.deque([((0, 0), (0, 0))] * LI)
        ck_next = 0
        while True:
            # The PHY builds CK from ck_en_next; it must be ck_en one cycle early (#628).
            assert ctx.get(p.ck_en) == ck_next, "ck_en_next does not lead ck_en by one cycle"
            ck_next = ctx.get(p.ck_en_next)
            out_q.append((ctx.get(p.ck_en), ctx.get(p.dq_o), ctx.get(p.rwds_o)))
            cs_q.append((ctx.get(p.cs), ctx.get(p.dq_oe), ctx.get(p.rwds_oe)))
            ck_en, dq_o, rwds_o = out_q.popleft()
            cs, dq_oe, rwds_oe = cs_q.popleft()
            # Undriven pins read 0; an undriven RWDS is a mask (worst case).
            dq_o = dq_o if dq_oe else 0
            rwds_o = rwds_o if rwds_oe else 3
            for half in dev.step(cs, ck_en, dq_o, rwds_o):
                ret_q.append(half)
            sample = (ret_q.popleft(), ret_q.popleft())
            in_q.append(sample)
            (d0, r0), (d1, r1) = in_q.popleft()
            ctx.set(p.dq_q0, d0)
            ctx.set(p.rwds_q0, r0)
            ctx.set(p.dq_q1, d1)
            ctx.set(p.rwds_q1, r1)
            await ctx.tick("hr")

    async def control(ctx):
        for _ in range(int(2 * 2000 * CK_MHZ / 1000 * SYNC_MHZ / CK_MHZ) + 50):
            await ctx.tick("sync")                    # RESET# sequence (2 x 2 us)
        for case in cases:
            ctx.set(eng.mode, case["mode"])
            ctx.set(eng.burst, case["burst"])
            ctx.set(eng.total, case["total"])
            ctx.set(eng.xor, case.get("xor", 0))
            ctx.set(eng.seed, case.get("seed", 0))
            ctx.set(eng.wait, case.get("wait", WAIT_DEFAULT))
            ctx.set(eng.reg_addr, 0x800)
            ctx.set(eng.start_addr, case.get("start", 0))
            ctx.set(eng.go, 1)
            await ctx.tick("sync")
            ctx.set(eng.go, 0)
            # Waits for: one run. Expected: total beats + ~40 CK per burst; x1.25; expiry fails the case.
            bursts = case["total"] // max(case["burst"], 1) + 1
            bound = int(1.25 * (case["total"] * (2 if case["mode"] == MODE_BOTH else 1)
                                + 2 * bursts * 40 + 64) * SYNC_MHZ / CK_MHZ) + 100
            finished = False
            for _ in range(bound):
                await ctx.tick("sync")
                if ctx.get(eng.done):
                    finished = True
                    break
            await ctx.tick("sync").repeat(40)           # checker drains
            row = dict(case, finished=finished)
            for k in ("run_cycles", "words_w", "words_r", "src_wait", "sink_wait", "xacts",
                      "missing", "straddled", "checked", "errors", "bad_index", "reg_value"):
                row[k] = ctx.get(getattr(eng, k))
            results.append(row)
            log.info("    %s", {k: row[k] for k in ("mode", "burst", "total", "run_cycles",
                     "words_w", "words_r", "missing", "straddled", "checked", "errors",
                     "reg_value", "finished")})

    sim = Simulator(Fragment.get(m, None))
    sim.add_clock(1 / (CK_MHZ * 1e6), domain="hr")
    sim.add_clock(1 / (SYNC_MHZ * 1e6), domain="sync")
    sim.add_testbench(board, background=True)
    sim.add_testbench(control)
    total_s = 5e-6 + sum((c["total"] * 2 + 2000) / (CK_MHZ * 1e6) * 1.5 for c in cases)
    sim.run_until(total_s)
    return core, dev, results


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(LOG, mode="w")])
    mb = max_burst_words(CK_MHZ)
    tcsm_cycles = int(T_CSM_NS * CK_MHZ / 1000)
    log.info("CK %g, sync %g MHz; max burst %d words; tCSM %d CK", CK_MHZ, SYNC_MHZ, mb, tcsm_cycles)
    failures = []

    def check(ok, what):
        log.info("  %s  %s", "PASS" if ok else "FAIL", what)
        if not ok:
            failures.append(what)

    cases = [
        dict(mode=MODE_REG, burst=2, total=2, tag="CR0 read"),
        dict(mode=MODE_WRITE, burst=mb, total=TOTAL, tag="write max burst"),
        dict(mode=MODE_READ, burst=mb, total=TOTAL, tag="read max burst"),
        dict(mode=MODE_READ, burst=mb, total=TOTAL, xor=1, tag="negative control: xor"),
        dict(mode=MODE_BOTH, burst=64, total=TOTAL, tag="write/read 64"),
        dict(mode=MODE_WRITE, burst=mb, total=TOTAL, wait=WAIT_DEFAULT - 1,
             tag="negative control: write latency one short"),
        dict(mode=MODE_READ, burst=mb, total=TOTAL, tag="read after wrong-latency write"),
        dict(mode=MODE_WRITE, burst=mb, total=TOTAL, tag="rewrite"),
        dict(mode=MODE_READ, burst=16, total=TOTAL, tag="read burst 16"),
        dict(mode=MODE_READ, burst=350, total=1024, start=6, tag="read unaligned start"),
        dict(mode=MODE_WRITE, burst=mb, total=TOTAL, seed=0x1234, tag="write seeded"),
        dict(mode=MODE_READ, burst=mb, total=TOTAL, seed=0x1234, tag="read seeded"),
        dict(mode=MODE_READ, burst=mb, total=TOTAL, seed=0, tag="negative control: stale seed"),
    ]
    for r_halves in (6, 7):
        log.info("round trip %d half-cycles (%s)", r_halves,
                 "aligned" if r_halves % 2 == 0 else "straddled")
        _, dev, results = simulate(cases, r_halves)
        for r in results:
            tag = f"R{r_halves} {r['tag']}"
            check(r["finished"], f"{tag}: finished in bound")
            if r["mode"] == MODE_REG:
                check(r["reg_value"] == CR0_DEFAULT, f"{tag}: CR0 = {r['reg_value']:#06x}")
                continue
            if r["mode"] & 2:
                check(r["missing"] == 0, f"{tag}: no missing words")
                check(r["checked"] == r["total"], f"{tag}: checker saw every word")
                expect_errors = r.get("xor") or "wrong-latency" in r["tag"] or "stale" in r["tag"]
                if r.get("xor"):
                    check(r["errors"] == r["total"], f"{tag}: every word fails")
                elif expect_errors:
                    check(r["errors"] > r["total"] // 2, f"{tag}: errors {r['errors']}")
                else:
                    check(r["errors"] == 0, f"{tag}: zero errors")
                check((r["straddled"] > 0) == (r_halves % 2 == 1), f"{tag}: alignment branch")
            if r["mode"] & 1:
                check(r["words_w"] == r["total"], f"{tag}: words written")
            if r["tag"] == "read max burst":
                log.info("    sim read efficiency %.1f%%", 100 * r["words_r"] / r["run_cycles"])
            if r["tag"] == "write max burst":
                log.info("    sim write efficiency %.1f%%", 100 * r["words_w"] / r["run_cycles"])
        wrong = [a for a in range(TOTAL) if dev.mem.get(a) != pattern_py(a) ^ 0x1234]
        check(not wrong, f"R{r_halves}: device memory == pattern ^ last seed" +
              (f" (first wrong {wrong[0]})" if wrong else ""))
        check(dev.cs_low_max <= tcsm_cycles,
              f"R{r_halves}: longest CS# Low {dev.cs_low_max} <= {tcsm_cycles} CK")
    log.info("%s: %d failure(s); log %s", "FAIL" if failures else "PASS", len(failures), LOG)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
