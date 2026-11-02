#
# The codec slot on each direction of the data path. Stage 1: pass-through.
# SPDX-License-Identifier: BSD-3-Clause

"""`CodecStage`: byte stream in, byte stream out, `capture.codec.byte_stream_signature`.

- Same stream shape as the capture codecs (#563, `gateware/soc/capture/codec.py`), so a
  `StoredCompressor`/`LZ4Decompressor` can sit here behind an adapter.
- Not their block signature: that is 1 KiB blocks with 12-bit lengths; data-path blocks
  are up to 64 KiB. The adapter (later) splits; `docs/data-path.md#codec-stage`.
- `codec_id` is the block's header AUX[3:0]; stage 1 ignores it (the RV rejects non-zero).
"""

from amaranth             import Module
from amaranth.lib         import wiring
from amaranth.lib.wiring  import In, Out

from capture.codec        import CODEC_ID_BITS, byte_stream_signature

__all__ = ["CodecStage"]


class CodecStage(wiring.Component):
    def __init__(self):
        super().__init__({
            "sink":     In(byte_stream_signature()),
            "source":   Out(byte_stream_signature()),
            "codec_id": In(CODEC_ID_BITS),
        })

    def elaborate(self, platform):
        m = Module()
        wiring.connect(m, wiring.flipped(self.sink), wiring.flipped(self.source))
        return m
