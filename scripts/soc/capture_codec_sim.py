#!/usr/bin/env python3
#
# Capture-path codecs (LZ4 block + stored): round trips, interop, corruption, throughput.
# SPDX-License-Identifier: BSD-3-Clause

"""Simulation of `gateware/soc/capture/`. Design: `docs/capture-compression.md`.

    python3 scripts/soc/capture_codec_sim.py
    python3 scripts/soc/capture_codec_sim.py --seed 123 --fuzz 500

Exit status 0 if every check passes. Output goes to the terminal and `tmp/logs/dev.log`.

Checks:
  - gateware compress -> Python decode (and `lz4` library decode) == input, corpus + fuzz
  - Python / `lz4` library encode -> gateware decompress == input (corpus + random
    valid blocks incl. overlap, long runs, extension bytes), window 1024 and 256
  - stored fallback taken exactly when the lz4 size is not smaller than the input
  - codec switching at block boundaries (both directions)
  - corrupt blocks end within the stated bound with `error`, no output past `raw_len`
  - throughput: input bytes per clock, per corpus entry, always-ready output
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

import _paths  # noqa: F401  (scripts/ subdirectories on sys.path)
from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402
import capture_codec_ref as ref  # noqa: E402

from amaranth.sim import Simulator  # noqa: E402

from capture.codec import (ID_LZ4, ID_STORED, StoredDecompressor,  # noqa: E402
                           SwitchableCompressor, SwitchableDecompressor)
from capture.lz4_compress import LZ4Compressor  # noqa: E402
from capture.lz4_decompress import LZ4Decompressor  # noqa: E402

CLOCK = 1e-8


class SimHang(Exception):
    pass


def compress_run(dut, blocks, *, rng, valid_prob=1.0, ready_prob=1.0, modes=None,
                 limits=None, max_cycles=None):
    """Feed `blocks`, collect coded blocks. Returns (results, accept_cycles)."""
    total = sum(len(b) for b in blocks)
    max_cycles = max_cycles or (40 * total + 4000 * len(blocks) + 20000)
    results, accept_cycle = [], []
    expected = len(blocks)

    async def feeder(ctx):
        cyc = 0
        for bi, blk in enumerate(blocks):
            if modes is not None:
                ctx.set(dut.mode, modes[bi])
            if limits is not None:
                ctx.set(dut.block_limit, limits[bi])
            for i, byte in enumerate(blk):
                while True:
                    if rng.random() < valid_prob:
                        ctx.set(dut.sink.valid, 1)
                        ctx.set(dut.sink.data, byte)
                        ctx.set(dut.sink.last, int(i == len(blk) - 1))
                        _, _, rdy = await ctx.tick().sample(dut.sink.ready)
                        cyc += 1
                        if rdy:
                            if i == 0:
                                accept_cycle.append(cyc)
                            break
                    else:
                        ctx.set(dut.sink.valid, 0)
                        await ctx.tick()
                        cyc += 1
        ctx.set(dut.sink.valid, 0)

    async def collector(ctx):
        cur, cyc = bytearray(), 0
        dbg = getattr(dut, "dbg_coded", None)
        while len(results) < expected:
            ctx.set(dut.source.ready, int(rng.random() < ready_prob))
            sig = [dut.source.valid, dut.source.ready, dut.source.data, dut.source.last,
                   dut.codec_id, dut.comp_len, dut.raw_len]
            if dbg is not None:
                sig.append(dbg)
            got = await ctx.tick().sample(*sig)
            cyc += 1
            if cyc > max_cycles:
                raise SimHang(f"compressor produced {len(results)}/{expected} blocks in {cyc} cycles")
            v, r, d, last, cid, cl, rl = got[2:9]
            if v and r:
                cur.append(d)
                if last:
                    results.append(dict(coded=bytes(cur), id=cid, comp_len=cl, raw_len=rl,
                                        dbg=got[9] if dbg is not None else None, cycle=cyc))
                    cur.clear()

    sim = Simulator(dut)
    sim.add_clock(CLOCK)
    sim.add_testbench(feeder)
    sim.add_testbench(collector)
    sim.run()
    return results, accept_cycle


def decompress_run(dut, blocks, *, rng, valid_prob=1.0, ready_prob=1.0, max_cycles=None):
    """`blocks`: [(coded, codec_id, raw_len)]. Returns per-block results at each `done`."""
    total = sum(len(b[0]) + b[2] for b in blocks)
    max_cycles = max_cycles or (30 * total + 4000 * len(blocks) + 20000)
    results, start_cycle = [], []
    expected = len(blocks)

    async def feeder(ctx):
        cyc = 0
        for coded, cid, raw_len in blocks:
            ctx.set(dut.codec_id, cid)
            ctx.set(dut.raw_len, raw_len)
            for i, byte in enumerate(coded):
                while True:
                    if rng.random() < valid_prob:
                        ctx.set(dut.sink.valid, 1)
                        ctx.set(dut.sink.data, byte)
                        ctx.set(dut.sink.last, int(i == len(coded) - 1))
                        _, _, rdy = await ctx.tick().sample(dut.sink.ready)
                        cyc += 1
                        if rdy:
                            if i == 0:
                                start_cycle.append(cyc)
                            break
                    else:
                        ctx.set(dut.sink.valid, 0)
                        await ctx.tick()
                        cyc += 1
            ctx.set(dut.sink.valid, 0)
            # next block's codec_id / raw_len are set only after this block is done,
            # i.e. when the results list has caught up
            while len(results) < len(start_cycle):
                await ctx.tick()
                cyc += 1
        ctx.set(dut.sink.valid, 0)

    async def collector(ctx):
        cur, last_seen, cyc = bytearray(), False, 0
        while len(results) < expected:
            ctx.set(dut.source.ready, int(rng.random() < ready_prob))
            got = await ctx.tick().sample(dut.source.valid, dut.source.ready, dut.source.data,
                                          dut.source.last, dut.done, dut.error)
            cyc += 1
            if cyc > max_cycles:
                raise SimHang(f"decompressor finished {len(results)}/{expected} blocks in {cyc} cycles")
            v, r, d, last, done, err = got[2:8]
            if v and r:
                cur.append(d)
                last_seen = last_seen or bool(last)
            if done:
                results.append(dict(raw=bytes(cur), last=last_seen, error=bool(err), cycle=cyc))
                cur.clear()
                last_seen = False

    sim = Simulator(dut)
    sim.add_clock(CLOCK)
    sim.add_testbench(feeder)
    sim.add_testbench(collector)
    sim.run()
    return results, start_cycle


def fuzz_blocks(rng, count):
    """Random-length blocks of mixed content: random, few-symbol, repeats, zero runs."""
    blocks = []
    for _ in range(count):
        n = rng.choice((rng.randrange(1, 40), rng.randrange(1, 200), rng.randrange(1, 1025),
                        rng.randrange(900, 1025)))
        kind = rng.randrange(6)
        if kind == 0:
            data = bytes(rng.randrange(256) for _ in range(n))
        elif kind == 1:
            data = bytes(rng.choice(b"\x00\x01\xff\xaa") for _ in range(n))
        elif kind == 2:
            unit = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40)))
            data = (unit * (n // len(unit) + 1))[:n]
        elif kind == 3:
            data = bytes([rng.randrange(256)]) * n
        else:
            buf = bytearray()
            while len(buf) < n:
                if buf and rng.random() < 0.5:
                    off = rng.randrange(1, min(len(buf), 1023) + 1)
                    for _ in range(rng.randrange(1, 60)):
                        buf.append(buf[-off])
                else:
                    buf += bytes(rng.randrange(256) for _ in range(rng.randrange(1, 20)))
            data = bytes(buf[:n])
        blocks.append(data)
    return blocks


def judge_compressed(r, data, window=1024, lib=None):
    """Return a string describing the first problem with one compressor result, or ''."""
    coded = r["coded"]
    if r["raw_len"] != len(data):
        return f"raw_len {r['raw_len']} != {len(data)}"
    if r["comp_len"] != len(coded):
        return f"comp_len {r['comp_len']} != actual {len(coded)}"
    if r["id"] == ID_STORED:
        return "" if coded == data else "stored block differs from input"
    if r["id"] != ID_LZ4:
        return f"unknown codec id {r['id']}"
    try:
        out = ref.lz4_decode(coded, window=window, raw_len=len(data))
    except ValueError as exc:
        return f"python decode: {exc}"
    if out != data:
        return "python decode differs"
    if lib is not None:
        try:
            if lib.decompress(coded, uncompressed_size=len(data)) != data:
                return "lz4 library decode differs"
        except Exception as exc:  # noqa: BLE001
            return f"lz4 library rejected the block: {exc}"
    return ""


def run_checks(checks, args):
    rng = random.Random(args.seed)
    lib = ref.lz4_library()
    emit(f"  seed {args.seed}; lz4 library {'present' if lib else 'ABSENT (library checks skipped)'}")
    corpus = ref.corpora(args.seed)

    # ------------------------------------------------ (a) compress -> python decode
    dut = LZ4Compressor()
    names = [n for n, _ in corpus]
    results, _ = compress_run(dut, [d for _, d in corpus], rng=rng)
    problems = []
    for (name, data), r in zip(corpus, results):
        why = judge_compressed(r, data, lib=lib)
        if why:
            problems.append(f"{name}: {why}")
    checks.check(f"(a) compress corpus ({len(corpus)} blocks) -> python/lz4 decode == input",
                 not problems, "; ".join(problems[:6]))
    for (name, data), r in zip(corpus, results):
        emit(f"      {name:<26} {len(data):>5} B -> {r['comp_len']:>5} B  id={r['id']}")

    fuzz = fuzz_blocks(rng, args.fuzz)
    dut = LZ4Compressor()
    results, _ = compress_run(dut, fuzz, rng=rng, valid_prob=0.8, ready_prob=0.7)
    problems = [f"fuzz[{i}] n={len(d)}: {why}"
                for i, (d, r) in enumerate(zip(fuzz, results))
                if (why := judge_compressed(r, d, lib=lib))]
    n_stored = sum(r["id"] == ID_STORED for r in results)
    checks.check(f"(a) compress {args.fuzz} fuzz blocks (random stalls both sides) == input",
                 not problems and len(results) == len(fuzz), "; ".join(problems[:6]),
                 measurement=f"{n_stored} stored, {len(fuzz) - n_stored} lz4")

    # window 256: every offset the compressor emits must be <= 256
    dut = LZ4Compressor(window=256)
    sub = fuzz[:100] + [d for _, d in corpus]
    results, _ = compress_run(dut, sub, rng=rng)
    problems = [f"#{i}: {why}" for i, (d, r) in enumerate(zip(sub, results))
                if (why := judge_compressed(r, d, window=256, lib=lib))]
    checks.check("(a) compressor window=256: every offset <= 256, round trip exact",
                 not problems, "; ".join(problems[:6]))

    # ------------------------------------------------ (c) stored fallback exactness
    dut = LZ4Compressor()
    blocks = [d for _, d in corpus] + fuzz[:150]
    results, _ = compress_run(dut, blocks, rng=rng)
    bad = []
    for i, (d, r) in enumerate(zip(blocks, results)):
        stored = r["id"] == ID_STORED
        if stored != (r["dbg"] >= len(d)):
            bad.append(f"#{i} n={len(d)} lz4_size={r['dbg']} id={r['id']}")
        if not stored and r["comp_len"] >= len(d):
            bad.append(f"#{i} lz4 block not smaller: {r['comp_len']} >= {len(d)}")
        if r["comp_len"] > len(d):
            bad.append(f"#{i} expanded: {r['comp_len']} > {len(d)}")
    stored_n = sum(r["id"] == ID_STORED for r in results)
    checks.check("(c) stored iff lz4 size >= input; output never larger than input",
                 not bad and 0 < stored_n < len(results), "; ".join(bad[:6]),
                 measurement=f"{stored_n}/{len(results)} blocks stored")
    sizes = {n: r for (n, _), r in zip(corpus, results)}
    checks.check("(c) incompressible 1 KiB block is stored, zero-fill 1 KiB is lz4",
                 sizes["incompressible-1k"]["id"] == ID_STORED
                 and sizes["all-zero-1k"]["id"] == ID_LZ4)

    # ------------------------------------------------ block_limit
    dut = LZ4Compressor()
    data = bytes(rng.randrange(4) for _ in range(300))
    results, _ = compress_run(dut, [data[:100], data[100:200], data[200:]], rng=rng,
                              limits=[100, 100, 100])
    # one 300-byte stream with block_limit=100 must come out as three blocks
    joined = b"".join(
        (r["coded"] if r["id"] == ID_STORED else ref.lz4_decode(r["coded"])) for r in results)
    checks.check("block_limit: blocks are split at the limit", joined == data
                 and [r["raw_len"] for r in results] == [100, 100, 100])

    # ------------------------------------------------ (f) throughput
    emit("")
    emit("  throughput (always-ready output, input valid every clock; steady state = blocks 2..R):")
    reps = 6
    rates = {}
    for name, data in corpus:
        if len(data) < 100:
            continue
        dut = LZ4Compressor()
        results, accept = compress_run(dut, [data] * reps, rng=rng)
        steady = accept[-1] - accept[1]                 # cycles for blocks 2..R-1 inputs
        per_block = steady / (reps - 2)
        rates[name] = len(data) / per_block
        emit(f"      {name:<26} {len(data):>5} B  {per_block:>7.1f} clk/block  "
             f"{rates[name]:.3f} B/clk")
    typical = {k: v for k, v in rates.items() if k not in ("incompressible-1k",)}
    checks.check("(f) throughput measured for every corpus entry >= 100 B",
                 len(rates) > 0 and min(typical.values()) > 0.5,
                 f"slowest {min(typical, key=typical.get)} {min(typical.values()):.3f} B/clk",
                 measurement=f"min {min(rates.values()):.3f}, max {max(rates.values()):.3f} B/clk; "
                             f"1 KiB blocks: {', '.join(f'{k} {v:.3f}' for k, v in rates.items() if '1k' in k)}")

    # ------------------------------------------------ (b) python encode -> gateware decode
    def lib_blocks(datas, window):
        out = []
        for d in datas:
            out.append((ref.lz4_encode(d, window=window), ID_LZ4, len(d)))
            if lib is not None:
                out.append((lib.compress(d, store_size=False), ID_LZ4, len(d)))
                out.append((lib.compress(d, store_size=False, mode="high_compression",
                                         compression=9), ID_LZ4, len(d)))
        return out

    datas = [d for _, d in corpus] + fuzz[:100]
    for window in (1024, 256):
        if window == 256:
            enc = [(ref.lz4_encode(d, window=256), ID_LZ4, len(d)) for d in datas]
        else:
            enc = lib_blocks(datas, 1024)
        want = []
        for d in datas:
            want += [d] * (len(enc) // len(datas))
        dut = LZ4Decompressor(window=window)
        results, _ = decompress_run(dut, enc, rng=rng, valid_prob=0.8, ready_prob=0.7)
        bad = [f"#{i}: n={len(w)} err={r['error']} last={r['last']} len={len(r['raw'])}"
               for i, (w, r) in enumerate(zip(want, results))
               if r["raw"] != w or r["error"] or not r["last"]]
        checks.check(f"(b) python{'+lz4 lib' if lib and window == 1024 else ''} encode -> "
                     f"gateware decompress == input, window {window} ({len(enc)} blocks)",
                     not bad, "; ".join(bad[:6]))

    valid = []
    n_valid = max(1, args.fuzz // 5)
    for _ in range(n_valid):
        blk, raw = ref.random_valid_block(rng, window=1024)
        valid.append((blk, ID_LZ4, len(raw), raw))
    dut = LZ4Decompressor()
    results, _ = decompress_run(dut, [v[:3] for v in valid], rng=rng, valid_prob=0.9, ready_prob=0.8)
    bad = [f"#{i}: raw_len={v[2]} err={r['error']} got {len(r['raw'])}"
           for i, (v, r) in enumerate(zip(valid, results))
           if r["raw"] != v[3] or r["error"] or not r["last"]]
    checks.check(f"(b) {n_valid} generated valid blocks (overlap, runs, long extensions) decode exactly",
                 not bad, "; ".join(bad[:6]))

    # decompressor throughput
    emit("  decompress throughput (raw bytes out per clock, one block, always ready):")
    dec_rates = {}
    for name, data in corpus:
        if len(data) < 100:
            continue
        coded = ref.lz4_encode(data)
        dut = LZ4Decompressor()
        res, start = decompress_run(dut, [(coded, ID_LZ4, len(data))] * 3, rng=rng)
        span = res[2]["cycle"] - res[1]["cycle"]
        dec_rates[name] = len(data) / span
        emit(f"      {name:<26} {len(data):>5} B  {span:>5} clk  {dec_rates[name]:.3f} B/clk")
    checks.check("(f) decompressor throughput measured", len(dec_rates) > 0,
                 measurement=f"min {min(dec_rates.values()):.3f}, max {max(dec_rates.values()):.3f} B/clk")

    # ------------------------------------------------ (d) mode switching
    modes = [rng.randrange(2) for _ in range(120)]
    sw_blocks = fuzz[:120]
    swc = SwitchableCompressor()
    results, _ = compress_run(swc, sw_blocks, rng=rng, modes=modes, valid_prob=0.9, ready_prob=0.8)
    bad = []
    for i, (d, r, mode) in enumerate(zip(sw_blocks, results, modes)):
        if mode == 0 and r["id"] != ID_STORED:
            bad.append(f"#{i} mode 0 gave id {r['id']}")
        if (why := judge_compressed(r, d, lib=lib)):
            bad.append(f"#{i}: {why}")
    ids_seen = {r["id"] for r in results}
    checks.check("(d) switchable compressor: mode picks the codec per block, order preserved",
                 not bad and ids_seen == {ID_STORED, ID_LZ4}, "; ".join(bad[:6]))
    sdec = SwitchableDecompressor()
    dec_in = [(r["coded"], r["id"], r["raw_len"]) for r in results]
    dres, _ = decompress_run(sdec, dec_in, rng=rng, valid_prob=0.9, ready_prob=0.8)
    bad = [f"#{i}" for i, (d, r) in enumerate(zip(sw_blocks, dres))
           if r["raw"] != d or r["error"] or not r["last"]]
    checks.check("(d) switchable decompressor: mixed stored/lz4 stream == input",
                 not bad and len(dres) == len(sw_blocks), "; ".join(bad[:6]))

    # ------------------------------------------------ (e) corrupt blocks
    def seqs(*parts):
        return b"".join(parts)

    lits = bytes(range(1, 21))
    good = ref.lz4_encode(lits + lits + bytes(30))
    cases = []
    # name, block, declared raw_len
    cases.append(("offset 0", bytes([0x40]) + lits[:4] + b"\x00\x00" + bytes([0x50]) + lits[:5], 14))
    cases.append(("offset beyond output", bytes([0x40]) + lits[:4] + b"\x09\x00" + bytes([0x50]) + lits[:5], 17))
    far = bytes(random.Random(5).randrange(256) for _ in range(300))
    cases.append(("offset beyond window (256)",
                  ref._sequence(far, 4, 280) + ref._sequence(bytes(12), None, None), 300 + 4 + 12))
    cases.append(("literal length beyond raw_len", bytes([0x90]) + lits[:9], 4))
    cases.append(("literal ext chain beyond raw_len", bytes([0xF0]) + b"\xff" * 20 + b"\x00" + lits[:5], 40))
    cases.append(("match length beyond raw_len", bytes([0x4F]) + lits[:4] + b"\x01\x00\xff\xff\xff\x00" + bytes([0x50]) + lits[:5], 30))
    cases.append(("truncated: no offset", bytes([0x44]) + lits[:4], 12))
    cases.append(("truncated: half an offset", bytes([0x44]) + lits[:4] + b"\x02", 12))
    cases.append(("truncated: literals cut", bytes([0x80]) + lits[:3], 8))
    cases.append(("truncated: match ext cut", bytes([0x4F]) + lits[:4] + b"\x02\x00", 12))
    cases.append(("raw_len larger than output", good, len(lits) * 2 + 30 + 5))
    cases.append(("raw_len smaller than output", good, len(lits) * 2 + 30 - 3))
    cases.append(("block ends in a match", bytes([0x40]) + lits[:4] + b"\x02\x00", 8))
    cases.append(("endless 255 match ext", bytes([0x4F]) + lits[:4] + b"\x01\x00" + b"\xff" * 400, 20))
    dut = LZ4Decompressor(window=256)
    seq_runs = []
    for name, blk, raw_len in cases:
        seq_runs.append((blk, ID_LZ4, raw_len))
    # a good block after every corrupt one proves the decoder resynchronises
    interleaved, labels = [], []
    ok_block = ref.lz4_encode(bytes(range(40)) * 2)
    for (name, blk, raw_len) in cases:
        interleaved.append((blk, ID_LZ4, raw_len)); labels.append(name)
        interleaved.append((ok_block, ID_LZ4, 80)); labels.append("good-after:" + name)
    results, starts = decompress_run(dut, interleaved, rng=rng)
    bad, worst = [], 0
    for i, ((blk, _, raw_len), label, r) in enumerate(zip(interleaved, labels, results)):
        is_good = label.startswith("good-after")
        if is_good:
            if r["error"] or r["raw"] != bytes(range(40)) * 2 or not r["last"]:
                bad.append(f"{label}: decoder did not recover")
            continue
        bound = len(blk) + raw_len + 2 * (len(blk) // 2 + 1) + 3
        dur = r["cycle"] - starts[i] + 1
        worst = max(worst, dur / bound)
        if not r["error"]:
            bad.append(f"{label}: error not set")
        if len(r["raw"]) > raw_len:
            bad.append(f"{label}: emitted {len(r['raw'])} > raw_len {raw_len}")
        if r["last"]:
            bad.append(f"{label}: last asserted on a corrupt block")
        if dur > bound:
            bad.append(f"{label}: took {dur} clk > bound {bound}")
    checks.check(f"(e) {len(cases)} corrupt-block cases: error set, no output past raw_len, "
                 f"ended within bound, next block decodes",
                 not bad, "; ".join(bad[:8]),
                 measurement=f"worst duration/bound = {worst:.2f}")
    # the same corrupt set through window 1024 (the offset>window case becomes offset>output)
    dut = LZ4Decompressor(window=1024)
    results, starts = decompress_run(dut, interleaved, rng=rng, valid_prob=0.7, ready_prob=0.6)
    bad = [f"{label}" for (blk, _, rl), label, r in zip(interleaved, labels, results)
           if not label.startswith("good-after") and "(256)" not in label
           and (not r["error"] or len(r["raw"]) > rl)]
    checks.check("(e) the same corrupt blocks under random stalls (window 1024)", not bad, "; ".join(bad[:8]))

    # stored decoder obeys the same raw_len bound
    ten = bytes(range(10))
    sres, _ = decompress_run(StoredDecompressor(), [(ten, ID_STORED, 10), (ten, ID_STORED, 8),
                                                    (ten, ID_STORED, 12), (ten, ID_STORED, 10)],
                             rng=rng)
    checks.check("(e) stored decoder: raw_len too small / too large -> error, good block after is clean",
                 [r["error"] for r in sres] == [False, True, True, False]
                 and len(sres[1]["raw"]) <= 8 and sres[0]["raw"] == ten and sres[3]["raw"] == ten,
                 f"{[(r['error'], len(r['raw'])) for r in sres]}")
    return rates


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=20261102)
    parser.add_argument("--fuzz", type=int, default=300)
    args = parser.parse_args()
    emit("capture codecs -- LZ4 block compressor / decompressor, stored, switching")
    emit(f"  fuzz blocks {args.fuzz}, seed {args.seed}")
    checks = Checks(emit)
    run_checks(checks, args)
    return checks.summary()


if __name__ == "__main__":
    raise SystemExit(main())
