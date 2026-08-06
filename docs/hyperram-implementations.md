# What other ECP5 HyperRAM implementations achieve, and how they calibrate

Prior art, gathered so nobody re-searches it. The per-option analysis for our own
part is in [`chips/w956a8-hyperram.md`](chips/w956a8-hyperram.md) and the ECP5
primitives are in [`chips/lfe5u-12f-ecp5.md`](chips/lfe5u-12f-ecp5.md).

**Index:** [`hardware.md`](hardware.md)

### The HyperRAM half of this claim is withdrawn

A survey of GitHub, GitLab, Codeberg and the FPGA blogs found nothing faster than
our figures on an ECP5, and for the flash nothing within 3×. **The flash half
stands. The HyperRAM half does not.**

The scoreboard below credits this board with **334.4 MB/s at CK 192**, and that
measurement is void — the pattern aliased 64 times across the part, the
controller latency was below the minimum CR0 requires, the JTAG readback slips,
and the negative control armed after the engine started. Re-measured, **CK 180
already fails in bulk**, and the highest figure that survives a live negative
control is **238.9 MB/s at CK 140**.

So this is not a record claim any more. Against the scoreboard's other entries
238.9 MB/s may still compare well, but that comparison has not been redone, and
claiming a record from a withdrawn number is how the original error propagated
into two upstream PR drafts.

### HyperRAM on ECP5 — the scoreboard

| project | part | device CK | peak | published measurement | read capture |
|---|---|---|---|---|---|
| **this board** | Winbond W956A8, LFE5U-12F | **140 MHz** | 280 MB/s | **238.9 MB/s** (334.4 at CK 192 **withdrawn**) | `DQSBUFM` 4:1 |
| DiVA, historic | LFE5U-25F-8, 1.8 V | 165 MHz | 330 MB/s | — | `IDDRX2F` + `DELAYF`, **no `DQSBUFM`** |
| orbtrace | LFE5U-25F | 150 MHz | 300 MB/s | — | `IDDRX2F` + `DELAYF` |
| DiVA, current | LFE5U-25F-8 | 150 MHz | 300 MB/s | ~194 MB/s sustained (inferred from its video load) | as above |
| boson-sd | LFE5U-25F-8 | 140 MHz | 280 MB/s | prints its own MB/s at boot | as above |
| **Tiliqua** | Cypress S27KL, LFE5U-45F | 120 MHz | 240 MB/s | *"tested up to 200 MB/sec"* | `DQSBUFM` 4:1 **+ READCLKSEL training** |
| **LUNA, pre-DQS** | — | 120 MHz | 240 MB/s | *"120 MHz DDR for a nominal rate of 1920 Mbit/s"* | `IDDRX1F` 2:1 |
| LiteX `hyperbus.py` | Certus-NX, **not ECP5** | 25 MHz | 50 MB/s | **46.7 MiB/s write, 22.7 read** | fabric SDR |

Our own upstream's published figure is the LUNA row — Great Scott Gadgets,
*"HyperRAM controller for USB analysis"*, 9 Feb 2022. **The DQS work has taken
that from 240 MB/s nominal to 238.9 MB/s measured under a live negative
control — a smaller gain than this page used to claim, and one that survives.**

Two clean negatives, so nobody re-searches: **ULX3S / Radiona have no HyperRAM at
all** (SDRAM and DDR3 boards), and **1BitSquared published no HyperRAM gateware
or numbers**. The related FUSBee5 board says *"Hyperram is now fully connected…
but still needs testing"* and never followed up.

**No ECP5 board in `litex-boards` calls `add_hyperram`.** Upstream LiteX's
HyperRAM core has never been tuned on this part; its ECP5 lineage is the separate
`litex-hub/litehyperbus`, Greg Davill's `HyperRAMX2`.

That absence is the load-bearing fact in
[`linux-on-cynthion.md`](linux-on-cynthion.md): `linux-on-litex-vexriscv` runs
Linux on ECP5 today, but nobody has run it out of HyperRAM. What that document
needs from this one is not the burst figure but the **per-transaction 19 CK
overhead**, because a 64-byte cache line refilled one 32-bit word at a time pays
it sixteen times — 36.6 MB/s by arithmetic, against 241 if the Wishbone window
coalesced the CTI burst. Unmeasured.

### Tiliqua has already implemented LUNA's TODO, and it is a drop-in

`apfaudio/tiliqua` vendors LUNA's `psram.py` split across three files and changed
**exactly one thing that matters**. Where LUNA hardcodes `READCLKSEL = 0b010`,
Tiliqua drives it from a runtime register with the mandatory `PAUSE`-before /
`PAUSE`-after sequence, and runs a training FSM (`periph/psram.py:198-223`):

    with m.If(timeout == 127):
        m.d.sync += counter.eq(counter + 1)
        with m.If(counter == 127):
            m.next = "IDLE"
        with m.If(~psram.phy.burstdet):
            m.d.sync += readclksel.eq(readclksel + 1)
            m.d.sync += counter.eq(0)

Dummy read, wait, check `BURSTDET`; if low, increment `READCLKSEL` (wrapping
0→7) and restart. It requires **128 consecutive bursts with `BURSTDET` high**
before releasing — matching TN-02035's recommendation exactly. Commit
`37180a74`, September 2024.

**This is the single most reusable thing found.** Same file lineage, same
primitive, proven on real ECP5 HyperRAM silicon — and it establishes that
`BURSTDET` *does* assert on a HyperBus part, which was the open question.

Two caveats before copying it wholesale:

- **Tiliqua runs at CK 120 MHz**, 40% below where we already are, so it is not
  evidence about 192 or 200.
- **First-pass-wins is the wrong policy.** It stops at the first `READCLKSEL`
  that works, which may be the edge of the eye. `jeanthom/gram`
  (`libgram/src/calibration.c`) does it properly: sweep 0..7, find the **minimum**
  and **maximum** values that assert, and program the **midpoint**. LiteX's BIOS
  does the same with `delay_mid = (delay_min + delay_max) / 2` and a comment
  worth keeping — `delay_min = delay - 1; // delay on edges can be spotty`.

Everything else in Tiliqua is unchanged from LUNA, including
`LOW/HIGH_LATENCY_CLOCKS = 3/5`, the `extra_latency | 1`, and the tied-off margin
control. **Neither LUNA nor Tiliqua writes CR0 at all**, so every option in the
register sections above is unexplored by both.
