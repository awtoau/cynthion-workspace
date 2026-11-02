#!/usr/bin/env python3
#
# Pure-Python LZ4 block reference (the oracle for gateware/soc/capture/).
# SPDX-License-Identifier: BSD-3-Clause

"""LZ4 block-format reference encoder/decoder and test corpora.

- Spec: token (lit nibble | match nibble), literals, LE16 offset, 255-chain
  extensions, min match 4, last 5 bytes literal, last match starts >= 12 B
  before the end.
- `lz4_decode` is strict: raises `ValueError` on any malformed block.
- `lz4_library()` returns `lz4.block` when installed, else None.
- `corpora()` builds the named test corpus; `random_valid_block()` builds a
  valid block directly (exercises long runs, overlap and extension bytes).

    python3 scripts/soc/capture_codec_ref.py     # self-check vs the lz4 library
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

MIN_MATCH = 4
LAST_LITERALS = 5
MFLIMIT = 12


def lz4_library():
    try:
        import lz4.block as block
        return block
    except ImportError:
        return None


def _ext(value: int) -> bytes:
    """255-chain bytes for `value` (the part beyond the 15 held in the nibble)."""
    out = bytearray()
    value -= 15
    while value >= 255:
        out.append(255)
        value -= 255
    out.append(value)
    return bytes(out)


def ext_len(value: int) -> int:
    return 0 if value < 15 else (value - 15) // 255 + 1


def lz4_decode(block: bytes, window: int = 65535, raw_len: int | None = None) -> bytes:
    out = bytearray()
    i, n = 0, len(block)
    if n == 0:
        raise ValueError("empty block")
    while True:
        if i >= n:
            raise ValueError("truncated: no token")
        token = block[i]; i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                if i >= n:
                    raise ValueError("truncated literal length")
                b = block[i]; i += 1
                lit += b
                if b != 255:
                    break
        if i + lit > n:
            raise ValueError("truncated literals")
        out += block[i:i + lit]; i += lit
        if i == n:
            break
        if i + 2 > n:
            raise ValueError("truncated offset")
        off = block[i] | (block[i + 1] << 8); i += 2
        if off == 0 or off > len(out) or off > window:
            raise ValueError(f"bad offset {off} at out={len(out)}")
        mlen = token & 15
        if mlen == 15:
            while True:
                if i >= n:
                    raise ValueError("truncated match length")
                b = block[i]; i += 1
                mlen += b
                if b != 255:
                    break
        mlen += MIN_MATCH
        for _ in range(mlen):
            out.append(out[-off])
        if raw_len is not None and len(out) > raw_len:
            raise ValueError("output beyond raw_len")
    if raw_len is not None and len(out) != raw_len:
        raise ValueError(f"raw_len {raw_len} != {len(out)}")
    return bytes(out)


def _sequence(lits: bytes, match_len: int | None, off: int | None) -> bytes:
    lit_n = len(lits)
    out = bytearray()
    mnib = 0 if match_len is None else min(match_len - MIN_MATCH, 15)
    out.append((min(lit_n, 15) << 4) | mnib)
    if lit_n >= 15:
        out += _ext(lit_n)
    out += lits
    if match_len is not None:
        out += bytes((off & 255, off >> 8))
        if match_len - MIN_MATCH >= 15:
            out += _ext(match_len - MIN_MATCH)
    return bytes(out)


def lz4_encode(data: bytes, window: int = 1024) -> bytes:
    """Greedy valid LZ4 block (spec end rules honoured)."""
    n = len(data)
    out = bytearray()
    table: dict[bytes, int] = {}
    anchor, i = 0, 0
    while i + MFLIMIT <= n:
        key = data[i:i + 4]
        cand = table.get(key)
        table[key] = i
        if cand is not None and i - cand <= window:
            mlen = 4
            while i + mlen < n - LAST_LITERALS and data[cand + mlen] == data[i + mlen]:
                mlen += 1
            out += _sequence(data[anchor:i], mlen, i - cand)
            i += mlen
            anchor = i
        else:
            i += 1
    out += _sequence(data[anchor:], None, None)
    return bytes(out)


def sequence_sizes(data: bytes, window: int = 1024) -> int:
    return len(lz4_encode(data, window))


def random_valid_block(rng: random.Random, window: int = 1024, max_raw: int = 1024):
    """(block, raw) built sequence by sequence; covers overlap, runs, extensions."""
    raw = bytearray()
    blk = bytearray()
    while True:
        want_end = len(raw) > 0 and rng.random() < 0.18
        if want_end or len(raw) >= max_raw - 40:
            kind = rng.choice((12, 12, 12, 16, 20, 300))
            lits = bytes(rng.randrange(256) for _ in range(min(kind, max_raw - len(raw))))
            blk += _sequence(lits, None, None)
            raw += lits
            return bytes(blk), bytes(raw)
        lit_n = rng.choice((0, 0, 1, 3, 7, 14, 15, 16, 40, 269, 270, 300))
        lit_n = min(lit_n, max(0, max_raw - 40 - len(raw)))
        lits = bytes(rng.choice((0, 255, rng.randrange(256))) for _ in range(lit_n))
        raw += lits
        if not raw:
            lits = bytes([rng.randrange(256)])
            raw += lits
        off = rng.choice((1, 1, 2, 3, 4, rng.randrange(1, min(len(raw), window) + 1)))
        off = min(off, len(raw), window)
        mlen = rng.choice((4, 4, 5, 8, 18, 19, 20, 100, 273, 274, 275, 530))
        mlen = min(mlen, max_raw - 5 - len(raw))
        if mlen < MIN_MATCH:
            blk += _sequence(lits, None, None)
            return bytes(blk), bytes(raw)
        blk += _sequence(lits, mlen, off)
        for _ in range(mlen):
            raw.append(raw[-off])


def _aoat_frames():
    """AOAT frames from awto-dji's testkit when a checkout is reachable (optional)."""
    for root in (Path(__file__).resolve().parents[3] / "awto-dji",):
        kit = root / "scripts" / "re" / "aoa_testkit.py"
        if kit.is_file():
            try:
                sys.path.insert(0, str(kit.parent))
                import aoa_testkit  # noqa: F401
                frames = getattr(aoa_testkit, "FRAMES", None)
                if frames:
                    return [(f"aoat{i}", bytes(f)) for i, f in enumerate(frames)]
            except Exception:  # noqa: BLE001 -- optional input, never a dependency
                return []
    return []


def corpora(seed: int = 1):
    """[(name, bytes)]: the named corpus."""
    rng = random.Random(seed)
    frame = bytes(rng.randrange(256) for _ in range(12))
    pattern = bytes(range(37)) + bytes(37)
    tail = bytes(rng.randrange(256) for _ in range(40)) + bytes(40) + bytes(rng.randrange(256) for _ in range(5))
    corp = [
        ("one-byte", b"\x5a"),
        ("two-bytes", b"\x00\x00"),
        ("twelve-zero", bytes(12)),
        ("thirteen-zero", bytes(13)),
        ("zlp-like-short", b"\xd2\x00\x10"),
        ("all-zero-1k", bytes(1024)),
        ("all-ones-1k", b"\xff" * 1024),
        ("alternating-1k", b"\xaa\x55" * 512),
        ("alternating-1023", (b"\xaa\x55" * 512)[:1023]),
        ("frames12-1k", (frame * 86)[:1024]),
        ("frames12-300", (frame * 25)[:300]),
        ("pattern74-1k", (pattern * 14)[:1024]),
        ("incompressible-1k", bytes(rng.randrange(256) for _ in range(1024))),
        ("random-100", bytes(rng.randrange(256) for _ in range(100))),
        ("ends-in-long-match", bytes(rng.randrange(256) for _ in range(64)) * 1 + bytes(range(64)) + bytes(rng.randrange(256) for _ in range(5)) + bytes(range(64))),
        ("run-then-random-tail", bytes(900) + bytes(rng.randrange(256) for _ in range(124))),
        ("match-candidate-at-end", tail + tail[:40]),
        ("far-repeat-1000", (lambda r: r + r[:24])(bytes(rng.randrange(256) for _ in range(1000)))),
        ("far-repeat-1023", (lambda r: r[:1023 - 4] + r[:4])(bytes(rng.randrange(256) for _ in range(1023)))),
    ]
    return corp + _aoat_frames()


def _selfcheck() -> int:
    rng = random.Random(7)
    lib = lz4_library()
    bad = 0
    for name, data in corpora():
        blk = lz4_encode(data)
        ok = lz4_decode(blk, raw_len=len(data)) == data
        if lib:
            ok = ok and lib.decompress(blk, uncompressed_size=max(len(data), 1)) == data if data else ok
            ok = ok and lz4_decode(lib.compress(data, store_size=False, mode="high_compression",
                                                compression=9), window=65535) == data
        print(f"{'PASS' if ok else 'FAIL'}  ref {name} ({len(data)} -> {len(blk)})")
        bad += not ok
    for i in range(300):
        blk, raw = random_valid_block(rng)
        ok = lz4_decode(blk, raw_len=len(raw)) == raw
        if lib:
            ok = ok and lib.decompress(blk, uncompressed_size=len(raw)) == raw
        bad += not ok
    print(f"{'FAIL' if bad else 'PASS'}  ref random valid blocks vs lz4 library: {lib is not None}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_selfcheck())
