#!/usr/bin/env python3
#
# Capture framer / ring / LZ4 block path: per-transaction records, ts16 rollover, overflow.
# SPDX-License-Identifier: BSD-3-Clause

"""Simulation of `gateware/soc/capture/{framer,path}.py`. Design: `docs/capture-compression.md`.

    python3 scripts/soc/capture_framer_sim.py [--seed N]

Traffic is host-side USB packets (PIDs, token CRC5, data CRC16 from the usb_host sims).
Checks:
  - one record per transaction (token/data/handshake grouped), exact bytes, flags, ts16
  - records decode back to absolute time exactly across ts16 rollover; WRAP records bound gaps
  - blocks are record aligned, <= 1024 B, first-record ts == `blk_ts`
  - ring full: records dropped whole, DROP records account for every one, counters exact
  - input FIFO overflow resynchronises on the next packet
  - full path: LZ4/stored blocks -> gateware decompress == ring bytes, python decode agrees
Output goes to the terminal and `tmp/logs/dev.log`.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

import _paths  # noqa: F401,E402
from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402
import capture_codec_ref as ref  # noqa: E402
import capture_codec_sim as cc  # noqa: E402
from usb_host_shim_sim import data_packet_bytes, token_bytes  # noqa: E402
from usb_host_sie_sim import (PID_ACK, PID_DATA0, PID_DATA1, PID_IN, PID_NAK,  # noqa: E402
                              PID_OUT, PID_SETUP, PID_SOF, crc5)

from amaranth.sim import Simulator  # noqa: E402

from capture.codec import ID_LZ4, ID_STORED, SwitchableDecompressor  # noqa: E402
from capture.framer import (HDR_BYTES, KIND_DROP, KIND_EVENT, KIND_TXN, KIND_WRAP,  # noqa: E402
                            SNAP_BYTES, WRAP_AGE, CaptureRing)
from capture.path import CapturePath  # noqa: E402

CLOCK = 1e-8
PID_STALL, PID_NYET = 0x1E, 0x96
CTRS = ("n_pkt", "n_sof", "n_nak", "n_err", "n_dropped", "n_lost")
MAX_GAP = WRAP_AGE + 4096     # allowed ts distance between consecutive records


# ---------------------------------------------------------------- packets and traffic

def token(pid, addr, ep):
    return bytes([pid] + token_bytes(addr, ep))


def sof(frame):
    value = frame & 0x7FF
    word = value | (crc5(value) << 11)
    return bytes([PID_SOF, word & 0xFF, word >> 8])


def data(pid, payload):
    return bytes([pid]) + bytes(data_packet_bytes(payload))


def hs(pid):
    return bytes([pid])


def payload_of(rng, size):
    """Mixed content: random, zero fill, counting pattern, repeated text."""
    kind = rng.randrange(4)
    if kind == 0:
        return bytes(rng.randrange(256) for _ in range(size))
    if kind == 1:
        return bytes(size)
    if kind == 2:
        return bytes((i * 3) & 0xFF for i in range(size))
    return (b"AOA-accessory-frame-" * (size // 20 + 1))[:size]


def gen_traffic(rng, count, big=True):
    """Items: ('txn', [(bytes, err)], event_code_in_gap|None), ('event', code), ('idle', n)."""
    items, frame, toggle = [], 0, 0
    for i in range(count):
        if i % 12 == 0:
            items.append(("txn", [(sof(frame), False)], None))
            frame += 1
        addr, ep = rng.choice((1, 5, 0x12)), rng.choice((0, 1, 2))
        kind = rng.randrange(11)
        pid = PID_DATA1 if toggle else PID_DATA0
        toggle ^= 1
        size = rng.choice((0, 1, 8, 31, 64, 200, 512)) if big else rng.choice((0, 8, 64))
        payload = payload_of(rng, size)
        if kind <= 1:
            pk = [token(PID_IN, addr, ep), hs(PID_NAK)]
        elif kind == 2:
            pk = [token(PID_IN, addr, ep), data(pid, payload), hs(PID_ACK)]
        elif kind == 3:
            pk = [token(PID_OUT, addr, ep), data(pid, payload), hs(PID_ACK)]
        elif kind == 4:
            pk = [token(PID_OUT, addr, ep), data(pid, payload), hs(PID_NAK)]
        elif kind == 5:
            pk = [token(PID_SETUP, addr, 0), data(PID_DATA0, payload[:8].ljust(8, b"\0")), hs(PID_ACK)]
        elif kind == 6:
            pk = [token(PID_IN, addr, ep)]                       # no response
        elif kind == 7:
            pk = [token(PID_OUT, addr, ep), data(pid, payload)]  # no handshake (isochronous)
        elif kind == 8:
            pk = [token(PID_IN, addr, ep), hs(PID_STALL)]
        elif kind == 9:
            pk = [token(PID_IN, addr, ep), data(pid, payload), hs(PID_NYET)]
        else:
            pk = [token(PID_OUT, addr, ep), data(pid, payload), hs(PID_ACK)]
        errs = [False] * len(pk)
        if kind == 2 and rng.random() < 0.3:
            errs[1] = True                                       # RxError on the data packet
        ev = rng.randrange(1, 31) if len(pk) > 1 and rng.random() < 0.08 else None
        items.append(("txn", list(zip(pk, errs)), ev))
        if rng.random() < 0.05:
            items.append(("event", rng.randrange(1, 31)))
    return items


def class_of(first_byte):
    ok = (first_byte >> 4) == (~first_byte & 0xF)
    low = first_byte & 3
    if ok and low == 3:
        return "data"
    if ok and low == 2:
        return "hs"
    return "tok"


def expect_txn(pkts, ts):
    classes = [class_of(b[0]) for b, _ in pkts]
    payload = b"".join(b for b, _ in pkts)
    err = any(e for _, e in pkts) or any(c == "tok" and len(b) != 3 for (b, _), c in zip(pkts, classes))
    is_sof = pkts[0][0][0] == PID_SOF
    return dict(ts=ts, kind=KIND_TXN, err=err, to=("hs" not in classes) and not is_sof,
                trunc=len(payload) > SNAP_BYTES, tok="tok" in classes, data="data" in classes,
                hs="hs" in classes, payload=payload[:SNAP_BYTES])


def expect_event(ts, code):
    return dict(ts=ts, kind=KIND_EVENT, err=False, to=False, trunc=False, tok=False, data=False,
                hs=False, payload=bytes([code]))


# ---------------------------------------------------------------- record decoding

def parse_records(raw):
    out, i = [], 0
    while i < len(raw):
        if i + HDR_BYTES > len(raw):
            raise ValueError(f"truncated header at {i}")
        ts, fl = raw[i] | raw[i + 1] << 8, raw[i + 2]
        n = raw[i + 3] | raw[i + 4] << 8
        if i + HDR_BYTES + n > len(raw):
            raise ValueError(f"record at {i} overruns the block ({n} B)")
        out.append(dict(ts=ts, kind=fl & 3, err=bool(fl & 4), to=bool(fl & 8), trunc=bool(fl & 16),
                        tok=bool(fl & 32), data=bool(fl & 64), hs=bool(fl & 128),
                        payload=bytes(raw[i + HDR_BYTES:i + HDR_BYTES + n])))
        i += HDR_BYTES + n
    return out


def unwrap(records, start=None):
    """Absolute times from ts16 by modular deltas; returns (abs list, worst gap, ok)."""
    absolute, prev, t, worst = [], start, 0, 0
    for r in records:
        if prev is not None:
            delta = (r["ts"] - prev) & 0xFFFF
            worst = max(worst, delta)
            t += delta
        absolute.append(t)
        prev = r["ts"]
    return absolute, worst


KEYS = ("ts", "kind", "err", "to", "trunc", "tok", "data", "hs", "payload")


def same(a, b):
    return all(a[k] == b[k] for k in KEYS)


# ---------------------------------------------------------------- bench

class SimHang(Exception):
    pass


def run_dut(dut, items, *, rng, byte_period=1, intra=(1, 24), inter=(11, 120), ready_prob=1.0,
            stall_cycles=0, quiet=64, max_cycles=3_000_000, mode_at=None):
    """Feed `items`, collect blocks. Returns dict(blocks, expected, firsts, counters, cycles)."""
    st = dict(done=False, cyc=0, last_act=0)
    expected, blocks, firsts, ctr = [], [], [], {}
    has_codec = hasattr(dut, "codec_id")

    async def step(ctx, valid=0, d=0, last=0, err=0, ev=0, code=0):
        ctx.set(dut.pkt_valid, valid); ctx.set(dut.pkt_data, d)
        ctx.set(dut.pkt_last, last);   ctx.set(dut.pkt_err, err)
        ctx.set(dut.ev_valid, ev);     ctx.set(dut.ev_code, code)
        if mode_at is not None:
            ctx.set(dut.mode, mode_at(st["cyc"]))
        _, _, now = await ctx.tick().sample(dut.now)
        st["cyc"] += 1
        return now

    async def idle(ctx, n):
        for _ in range(n):
            await step(ctx)

    async def feeder(ctx):
        nonlocal intra, inter
        for item in items:
            if item[0] == "idle":
                await idle(ctx, item[1])
                continue
            if item[0] == "gaps":
                intra, inter = item[1], item[2]
                continue
            if item[0] == "mark":
                st["mark"] = len(expected)
                continue
            if item[0] == "event":
                await idle(ctx, rng.randint(*inter))
                at = st["cyc"]
                now = await step(ctx, ev=1, code=item[1])
                expected.append(dict(expect_event(now, item[1]), cyc=at))
                continue
            _, pkts, ev_code = item
            await idle(ctx, rng.randint(*inter))
            first_ts, ev_now, first_cyc = None, None, st["cyc"]
            for pi, (pkt, err) in enumerate(pkts):
                for bi, byte in enumerate(pkt):
                    now = await step(ctx, valid=1, d=byte, last=int(bi == len(pkt) - 1),
                                     err=int(err and bi == len(pkt) - 1))
                    if first_ts is None:
                        first_ts = now
                    await idle(ctx, byte_period - 1)
                if pi == 0 and ev_code is not None:
                    await idle(ctx, 2)
                    ev_at = st["cyc"]
                    ev_now = await step(ctx, ev=1, code=ev_code)
                if pi < len(pkts) - 1:
                    await idle(ctx, rng.randint(*intra))
            expected.append(dict(expect_txn(pkts, first_ts), cyc=first_cyc))
            if ev_code is not None:
                expected.append(dict(expect_event(ev_now, ev_code), cyc=ev_at))
        st["done"] = True
        while True:
            ctx.set(dut.flush, 1)
            await step(ctx)

    async def collector(ctx):
        cur, cyc = bytearray(), 0
        while True:
            ready = cyc >= stall_cycles and rng.random() < ready_prob
            ctx.set(dut.source.ready, int(ready))
            sig = [dut.source.valid, dut.source.data, dut.source.last, dut.ring_used,
                   dut.blk_ts_valid, dut.blk_ts]
            sig += [getattr(dut, k) for k in CTRS]
            if has_codec:
                sig += [dut.codec_id, dut.comp_len, dut.raw_len]
            got = await ctx.tick().sample(*sig)
            ctr.update(zip(CTRS, got[8:8 + len(CTRS)]))
            cyc += 1
            if cyc > max_cycles:
                raise SimHang(f"no completion after {cyc} cycles ({len(blocks)} blocks)")
            v, d, last, used, tsv, ts = got[2:8]
            if tsv:
                firsts.append(ts)
            if v and ready:
                st["last_act"] = cyc
                cur.append(d)
                if last:
                    b = dict(raw=bytes(cur), cycle=cyc)
                    if has_codec:
                        b.update(id=got[-3], comp_len=got[-2], raw_len=got[-1])
                    blocks.append(b)
                    cur.clear()
            if st["done"] and used == 0 and not cur and cyc - st["last_act"] > quiet:
                return

    sim = Simulator(dut)
    sim.add_clock(CLOCK)
    sim.add_testbench(feeder, background=True)
    sim.add_testbench(collector)
    sim.run()
    return dict(blocks=blocks, expected=expected, firsts=firsts, cycles=st["cyc"], ctr=ctr,
                mark=st.get("mark"))


def split_records(blocks):
    """Parse every block alone; returns (all records, problems)."""
    out, bad = [], []
    for i, b in enumerate(blocks):
        try:
            recs = parse_records(b["raw"])
        except ValueError as exc:
            bad.append(f"block {i}: {exc}")
            continue
        if len(b["raw"]) > 1024:
            bad.append(f"block {i}: {len(b['raw'])} B")
        out.append(recs)
    return out, bad


def expected_totals(items):
    pk = [p for it in items if it[0] == "txn" for p in it[1]]
    return dict(n_pkt=len(pk), n_sof=sum(b[0] == PID_SOF for b, _ in pk),
                n_nak=sum(b[0] == PID_NAK for b, _ in pk), n_err=sum(e for _, e in pk))


# ---------------------------------------------------------------- scenarios

def scenario_mixed(checks, rng, seed):
    emit("  -- mixed HS traffic, ts16 rolling over during the run")
    items = gen_traffic(rng, 140)
    totals = expected_totals(items)
    dut = CaptureRing(ts_init=0xFF00)
    res = run_dut(dut, items, rng=rng)
    per_block, bad = split_records(res["blocks"])
    flat = [r for recs in per_block for r in recs]
    real = [r for r in flat if r["kind"] != KIND_WRAP]
    checks.check("blocks parse alone, are record aligned and <= 1024 B", not bad and len(per_block) > 3,
                 "; ".join(bad[:4]), measurement=f"{len(per_block)} blocks, {len(flat)} records")
    exp = res["expected"]
    mism = [i for i, (a, b) in enumerate(zip(real, exp)) if not same(a, b)]
    checks.check("one record per transaction/event: count, bytes, flags, ts16 exact",
                 len(real) == len(exp) and not mism,
                 f"decoded {len(real)} expected {len(exp)} first mismatch {mism[:3]}")
    kinds = {k: sum(r["kind"] == k for r in real) for k in (KIND_TXN, KIND_EVENT)}
    pk_total = totals["n_pkt"]
    checks.check("transactions group packets (fewer records than packets)",
                 kinds[KIND_TXN] < pk_total and any(r["tok"] and r["data"] and r["hs"] for r in real)
                 and any(r["tok"] and not r["hs"] and r["to"] for r in real),
                 f"{kinds[KIND_TXN]} txn records for {pk_total} packets",
                 measurement=f"{kinds[KIND_TXN]} txn + {kinds[KIND_EVENT]} event records, {pk_total} packets")
    bigs = [r for r in real if r["kind"] == KIND_TXN and r["data"] and len(r["payload"]) > 500]
    checks.check("512 B bulk transaction is one record", len(bigs) > 0 and len(bigs[0]["payload"]) >= 519)
    checks.check("counters equal the packet totals exactly",
                 all(res["ctr"][k] == totals[k] for k in totals) and res["ctr"]["n_dropped"] == 0,
                 f"{res['ctr']} vs {totals}")
    absolute, worst = unwrap(flat)
    ev_t = [r["ts"] for r in flat]
    checks.check("ts16 never goes backwards (modular delta < gap bound) across rollover",
                 worst < MAX_GAP and any(ev_t[i + 1] < ev_t[i] for i in range(len(ev_t) - 1)),
                 f"worst delta {worst:#x}")
    return res, dut, totals


def subsequence(real, exp):
    """Match decoded records against expected in order. Returns (matched, extra decoded)."""
    pos, matched, extra = 0, 0, []
    for r in real:
        while pos < len(exp) and not same(r, exp[pos]):
            pos += 1
        if pos == len(exp):
            extra.append(r)
        else:
            matched += 1
            pos += 1
    return matched, extra


def flat_records(res):
    per_block, bad = split_records(res["blocks"])
    flat = [r for recs in per_block for r in recs]
    return flat, [r for r in flat if r["kind"] not in (KIND_WRAP, KIND_DROP)], bad


def scenario_fs(checks, rng):
    emit("  -- full-speed byte rate (one byte per 40 clocks)")
    items = gen_traffic(rng, 14, big=False)
    res = run_dut(CaptureRing(), items, rng=rng, byte_period=40, intra=(5, 300), inter=(40, 400))
    flat, real, bad = flat_records(res)
    checks.check("FS-rate bytes: records exact", not bad and len(real) == len(res["expected"])
                 and all(same(a, b) for a, b in zip(real, res["expected"])),
                 f"{len(real)} vs {len(res['expected'])}; {bad[:2]}")


def scenario_idle_rollover(checks, rng):
    emit("  -- idle across several ts16 periods")
    head = gen_traffic(rng, 6, big=False)
    tail = gen_traffic(rng, 6, big=False)
    items = head + [("idle", 2 * 65536 + 1000)] + tail + [("event", 7)]
    res = run_dut(CaptureRing(ts_init=0xFFF0), items, rng=rng)
    flat, real, bad = flat_records(res)
    wraps = [r for r in flat if r["kind"] == KIND_WRAP]
    exp = res["expected"]
    ok_rec = not bad and len(real) == len(exp) and all(same(a, b) for a, b in zip(real, exp))
    checks.check("records exact across 2+ ts16 periods of idle", ok_rec,
                 f"{len(real)} vs {len(exp)}; {bad[:2]}")
    absolute, worst = unwrap(flat)
    real_abs = [a for a, r in zip(absolute, flat) if r["kind"] != KIND_WRAP]
    cyc = [e["cyc"] for e in exp]
    off = ([ra - real_abs[0] - (c - cyc[0]) for ra, c in zip(real_abs, cyc)] if ok_rec else [1])
    checks.check("absolute time rebuilt from ts16 equals the true clock distance",
                 ok_rec and not any(off), f"first offsets {off[:4]}")
    gap_true = max(b - a for a, b in zip(cyc, cyc[1:]))
    checks.check("a gap > 2^16 counts is recovered only through WRAP records",
                 gap_true > 0x10000 and len(wraps) >= 3 and worst < MAX_GAP,
                 f"true gap {gap_true}, {len(wraps)} WRAP records, worst ts delta {worst:#x}",
                 measurement=f"true gap {gap_true} counts, {len(wraps)} WRAP records")


def scenario_ring_full(checks, rng):
    emit("  -- ring full: whole records dropped, DROP record accounts for them")
    items = gen_traffic(rng, 90, big=False)
    res = run_dut(CaptureRing(ring=1024), items, rng=rng, stall_cycles=40000)
    flat, real, bad = flat_records(res)
    exp = res["expected"]
    matched, extra = subsequence(real, exp)
    drops = [r for r in flat if r["kind"] == KIND_DROP]
    counted = sum(r["payload"][0] for r in drops)
    checks.check("kept records are exact, in order, none partial",
                 not bad and not extra and matched == len(real) and 0 < matched < len(exp),
                 f"matched {matched}/{len(real)} extra {len(extra)} {bad[:2]}",
                 measurement=f"{len(exp) - matched} of {len(exp)} records dropped")
    checks.check("DROP records + kept == records fed; n_dropped agrees",
                 counted == len(exp) - matched == res["ctr"]["n_dropped"] and len(drops) >= 1,
                 f"drop records {counted}, missing {len(exp) - matched}, counter {res['ctr']['n_dropped']}")
    checks.check("oldest records survive (full ring drops new data, not old)",
                 matched > 0 and same(real[0], exp[0]))
    checks.check("packet counters still exact while records drop",
                 all(res["ctr"][k] == v for k, v in expected_totals(items).items()), f"{res['ctr']}")


def scenario_fifo_overflow(checks, rng):
    emit("  -- input FIFO overflow (back-to-back packets, tiny FIFO), then clean traffic")
    stress = gen_traffic(rng, 60, big=False)
    clean = gen_traffic(rng, 10, big=False)
    items = ([("gaps", (0, 0), (0, 0))] + stress
             + [("gaps", (12, 24), (60, 120)), ("idle", 3000), ("mark",)] + clean)
    res = run_dut(CaptureRing(fifo_depth=4), items, rng=rng)
    flat, real, bad = flat_records(res)
    tail_exp = res["expected"][res["mark"]:]
    checks.check("stream stays well formed after FIFO overflow", not bad and res["ctr"]["n_lost"] > 0,
                 f"{bad[:2]} lost={res['ctr']['n_lost']}")
    ok = len(real) >= len(tail_exp) and all(
        same(a, b) for a, b in zip(real[len(real) - len(tail_exp):], tail_exp))
    checks.check("traffic after the overflow is recorded exactly (resynchronised)", ok,
                 f"tail {len(tail_exp)} records, got {len(real)} total")


def scenario_path(checks, rng, small_ring=False):
    tag = "ring-full" if small_ring else "normal"
    emit(f"  -- full path, switchable LZ4 ({tag})")
    items = gen_traffic(rng, 60 if small_ring else 120)

    def plan(cyc):                       # a stored stretch in the middle
        return ID_STORED if 9000 <= cyc < 14000 else ID_LZ4

    dut = CapturePath(ring=1024 if small_ring else 2048, ts_init=0xFFE0)
    res = run_dut(dut, items, rng=rng, ready_prob=0.5 if small_ring else 1.0, quiet=2600, mode_at=plan,
                  stall_cycles=30000 if small_ring else 0)
    blocks = res["blocks"]
    lib = ref.lz4_library()
    raws, problems = [], []
    for i, b in enumerate(blocks):
        if b["id"] == ID_STORED:
            raw = b["raw"]
        else:
            try:
                raw = ref.lz4_decode(b["raw"], raw_len=b["raw_len"])
            except ValueError as exc:
                problems.append(f"block {i}: {exc}")
                raws.append(b"")
                continue
            if lib is not None and lib.decompress(b["raw"], uncompressed_size=b["raw_len"]) != raw:
                problems.append(f"block {i}: lz4 library disagrees")
        if len(raw) != b["raw_len"] or b["comp_len"] != len(b["raw"]):
            problems.append(f"block {i}: lengths")
        raws.append(raw)
    recs, bad = [], []
    for i, raw in enumerate(raws):
        try:
            recs.append(parse_records(raw))
        except ValueError as exc:
            bad.append(f"block {i}: {exc}")
    flat = [r for rr in recs for r in rr]
    real = [r for r in flat if r["kind"] not in (KIND_WRAP, KIND_DROP)]
    ids = {b["id"] for b in blocks}
    exp = res["expected"]
    matched, extra = subsequence(real, exp)
    if small_ring:
        counted = sum(r["payload"][0] for r in flat if r["kind"] == KIND_DROP)
        ok = matched == len(real) and not extra and counted == len(exp) - matched > 0
        what = "kept records exact + DROP accounts for the rest"
    else:
        ok = len(real) == len(exp) and matched == len(exp)
        what = "decompressed blocks hold every record, exact"
    checks.check(f"path {tag}: {what}", ok and not problems and not bad,
                 f"matched {matched}/{len(real)} of {len(exp)}; {problems[:2]} {bad[:2]} {res['ctr']}",
                 measurement=f"{len(blocks)} blocks, raw {sum(b['raw_len'] for b in blocks)} B -> "
                             f"coded {sum(b['comp_len'] for b in blocks)} B")
    checks.check(f"path {tag}: lz4 and stored blocks both present; blk_ts == first record ts",
                 ids == {ID_LZ4, ID_STORED} and res["firsts"][:len(recs)] == [rr[0]["ts"] for rr in recs],
                 f"ids {ids} firsts {len(res['firsts'])} blocks {len(recs)}")
    dec, _ = cc.decompress_run(SwitchableDecompressor(),
                               [(b["raw"], b["id"], b["raw_len"]) for b in blocks], rng=rng)
    bad_dec = [i for i, (d, raw) in enumerate(zip(dec, raws)) if d["raw"] != raw or d["error"]]
    checks.check(f"path {tag}: gateware decompressor == ring bytes for all {len(blocks)} blocks",
                 not bad_dec and len(dec) == len(raws), f"blocks {bad_dec[:5]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=20261102)
    args = parser.parse_args()
    emit("capture framer -- per-transaction ts16 records, ring, record-aligned LZ4 blocks")
    checks = Checks(emit)
    rng = random.Random(args.seed)
    scenario_mixed(checks, rng, args.seed)
    scenario_fs(checks, rng)
    scenario_idle_rollover(checks, rng)
    scenario_ring_full(checks, rng)
    scenario_fifo_overflow(checks, rng)
    scenario_path(checks, rng)
    scenario_path(checks, rng, small_ring=True)
    return checks.summary()


if __name__ == "__main__":
    raise SystemExit(main())
