# Decisions still open

What has **not** been settled, and what would settle it. Everything already
decided is architecture, and lives in
[`soc-architecture.md`](soc-architecture.md) — a table of what the SoC is made
of and where each piece came from, without the arguments that got there.

**Scope.** Technical choices. The *policy* on upstream code — what Great Scott
Gadgets code we reserve, and why — is
[`upstream-boundary.md`](upstream-boundary.md).

---

## Memory speed: what to do next, by return per effort

**Decision: for the flash, nothing — it is finished. For the HyperRAM, two register
writes before any design work.**

Per-option analysis: [`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md),
[`chips/w956a8-hyperram.md`](chips/w956a8-hyperram.md), and the ECP5 primitives in
[`chips/ecp5/lfe5u-12f.md`](chips/ecp5/lfe5u-12f.md).

**Do not rank against 334.4 MB/s at CK 192.** That figure is withdrawn — the pattern aliased 64 times
across the part, the controller latency was below CR0's minimum, and the negative
control armed after the engine started. **CK 180 fails in bulk with 4.7 M errors.**
The baseline below is 238.9 MB/s at CK 140, which is what survives a live control.

**Flash — closed.**

| rank | option | worth | effort |
|---|---|---|---|
| ✔ | `FLASH_MODE = "quad"` | **2.70×** measured | done in `03482f4`, for −261 LUTs |
| 1 | replace luna_soc's PHY (`SCK` capped at `sync`/2) | 2× | large, and the only one not gated on the CPU clock |
| 2 | raise `SYNC_MHZ` | 2× at 120 MHz | **gated by the RISC-V Fmax of 75 MHz**, not by the flash |
| 3 | 128-byte I-cache line | +7.2% | a parameter, plus block RAM already at 75% |
| 4 | `0xEB` continuous read in the SoC | +5.1% | small, but the mode is sticky across reconfiguration |
| — | **QPI, DTR, `0xC0`** | **absent on this die** | — |

Bulk quad reads already run at 99.6% of the four-lane theoretical maximum. There
is no efficiency left; only SCK, and the instrument runs out before the flash does.

**HyperRAM — the two cheap discriminators first.** Against 238.9 MB/s (85.3% of
the 280 MB/s pin rate at CK 140), tCSM caps efficiency near 96.7% — 270.6 MB/s —
because CS# may not stay Low beyond 4 µs. Longer bursts are worth ~2.8 of the
remaining points. **8.5 points are unexplained**: the arithmetic predicts 93.8%
for the burst length actually in use, and that gap has never been measured.

| rank | option | what it establishes | effort |
|---|---|---|---|
| 1 | differential clock `CR1[6] = 0` | removes threshold error from the sampling instant; the board is wired for it and nobody has tried it | **one register write** |
| 2 | drive strength `CR0[14:12]` | the `tDSS` finding makes this plausible | three register writes |
| 3 | `CLKOS2_CPHASE`/`FPHASE` sweep | alignment faults move in discrete steps, skew faults narrow continuously — **this is the discriminator** | bitstreams only |
| 4 | longer bursts inside tCSM | ~2.8 points, and it is the only throughput lever below the ceiling | a splitter in the controller |
| 5 | `READCLKSEL` training from Tiliqua | converts "works, reason unknown" into a measured eye | drop-in from a common ancestor |
| 6 | `CLKDIVF` + `ECLKSYNCB` + `ALIGNWD` | the only published open-source fix for a word-boundary slip on ECP5 | large; changes every HyperRAM bitstream |
| 7 | fit the 5I (200 MHz) part | if a 200 MHz-screened part *also* slips, the fault is the gearing, not the memory | rework; diagnostic before upgrade |
| — | lower initial latency count | **negative** — reading early is the failure mode | — |

**Do 1 and 2 first.** They cost no design work and they separate the two live
hypotheses — strobe skew against word-boundary alignment — which decides whether
anything below them is worth starting. Neither LUNA nor Tiliqua writes CR0 at all,
so there is no prior art to copy for either.

**Revisit when** the 8.5 unexplained points are measured. Ranking effort against a
gap nobody has instrumented is how the previous ranking came to recommend work at
a clock that does not pass.
## Open

| | state | tracked |
|---|---|---|
| HyperRAM DQS path | `HyperRAMWishbone` wraps the vendored controller at `0x2000_0000`, 8 MiB, `main=1 exe=1`. The DQS write path is unfinished, and the SoC currently ships the non-DQS one | #92, #211 |
| Board platform vendoring | in progress: `CynthionPlatformRev1D4` is 206 lines of pins plus a 134-line base, but reaching it inherits `LUNAApolloPlatform` → `LUNAPlatform` and pins the luna-soc fork. Target is a platform depending only on `amaranth`, `amaranth.build`, `amaranth_boards.resources` | — |
| `luna-soc` fork pin | the fork is zero commits behind upstream, so the pin buys nothing | #194 |
| I-cache size | 4 KiB direct-mapped, and RTIC's hot set does not fit it. The die is a 25F; block RAM is at 79% | #110 |

## Unverified

| claim | where | what is missing |
|---|---|---|
| Renode as an emulation alternative | — | never evaluated on the record |
| Verilator as a simulation alternative | — | never evaluated on the record |
| Type-C physical attach/detach | commit `bd7867b` | interrupt path verified; a real attach has not been exercised |
| The port request grants CONTROL | [`chips/cynone-sideband.md`](chips/cynone-sideband.md) | simulated frame-exact against Apollo's matcher; no bitstream built, and Apollo has never been put into `FPGA_ADV_MODE_UART` from the host |
| The bootloader's sideband byte | `firmware/cynthion-boot` | written on every boot and **never read back** — nothing host-side speaks the sideband protocol to Apollo. `scripts/sideband_decoder.py` decodes a reply; no tool fetches one |
