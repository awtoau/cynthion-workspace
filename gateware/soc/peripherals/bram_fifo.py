# amaranth's registered-read FIFOs with their storage pinned to block RAM.
# SPDX-License-Identifier: BSD-3-Clause

"""
`SyncFIFOBuffered`, `AsyncFIFO` and `AsyncFIFOBuffered` with `ram_style = "block"` on the storage.

- All three read their memory through a registered port, so they are already block-RAM shaped;
  yosys still maps a shallow one to distributed RAM (TRELLIS_DPR16X4) because that is cheaper
  for it. The attribute is the only difference: ports, levels and cycle behaviour are amaranth's.
- Proof of that, and of the mapping: `scripts/soc/bram_fifo_check.py`.
- Not for `SyncFIFO`: its read is combinational, see `usbcore/fifo.py`.
"""

from amaranth.lib.fifo import AsyncFIFO, AsyncFIFOBuffered, SyncFIFOBuffered

__all__ = ["BramSyncFIFOBuffered", "BramAsyncFIFO", "BramAsyncFIFOBuffered"]

BLOCK = {"ram_style": "block"}


def _pin(module):
    # Depth 0 and 1 have no storage.
    if hasattr(module.submodules, "storage"):
        module.submodules.storage.attrs.update(BLOCK)
    return module


class BramSyncFIFOBuffered(SyncFIFOBuffered):
    def elaborate(self, platform):
        return _pin(super().elaborate(platform))


class BramAsyncFIFO(AsyncFIFO):
    def elaborate(self, platform):
        return _pin(super().elaborate(platform))


class BramAsyncFIFOBuffered(AsyncFIFOBuffered):
    def elaborate(self, platform):
        m = super().elaborate(platform)
        # The inner AsyncFIFO elaborates later; pin its storage when it does.
        if hasattr(m.submodules, "unbuffered"):
            inner = m.submodules.unbuffered
            elaborate = inner.elaborate
            inner.elaborate = lambda platform: _pin(elaborate(platform))
        return m
