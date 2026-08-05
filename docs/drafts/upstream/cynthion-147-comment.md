We have DQS running on r1.4 and measured it. Two things that may be useful here,
one of which is probably why this issue has been quiet.

## `HyperRAMDQSPHY` cannot be instantiated on Amaranth 0.5

`luna.gateware.interface.psram.HyperRAMDQSPHY` (added in luna#236) is written
against the Amaranth 0.4 record API. On 0.5 it raises before it elaborates:

    AttributeError: 'Pin' object has no attribute 'io'

Three separate faults, all in the I/O layer rather than the protocol:

1. `bus.dq.io` / `bus.rwds.io` exist only on a raw (`dir="-"`) request. Under
   0.5, `platform.request()` returns `lib.io` port objects and reaching the pad
   — which the DQS primitives must do — needs `dir="-"` and `port.io`.
2. `o_Z=self.bus.clk` and `o_Z=self.bus.cs` drive a pad from inside a `DELAYG`,
   while `clk` and `cs` are written as if buffered. Under 0.5 they are ordinary
   output pins and the delay goes before the pin.
3. `bus.reset` is never driven — the record carries the field and elaboration
   ignores it, leaving RESET# floating.

It IS constructed — `applets/hyperram_diagnostic.py:54`, behind a module-level
`DQS = False` that has never been flipped. Flipping it does not work today, which
is consistent with the path never having been exercised since it landed. That
applet is the natural regression check for any fix.

There are three failure modes rather than one, reproduced on Amaranth 0.5.9: an
`AttributeError` on a plain `platform.request("ram")`, and two different
`TypeError`s — one on the applet's own request, one on a fully raw `dir="-"`
request.

One more, and it is the subtle one. `i_D0=~self.phy.cs` inverts by hand. That is
correct only while `cs` is requested raw, as the applet does: Cynthion declares
CS# as `PinsN` on every revision from r0.1, so once the pin is buffered the
manual `~` double-inverts and holds CS# **asserted forever**. Dropping it also
makes the DQS PHY agree with `HyperRAMPHY` fifty lines above it.

## Measured on r1.4

A gateware pattern engine (address-derived, no CPU in the loop), sustained
write/read/verify, 50 M words per rung, both PHYs on the same board and the same
harness so the only variable is the PHY and CK:

> **WITHDRAWN — every DQS figure below was measured with faulty instruments.**
> The pattern used only the low 16 address bits and so repeated 64 times across
> the part; the controller ran luna's `HIGH_LATENCY_CLOCKS = 5`, below the
> minimum of 6 that CR0's 14 CK requires, so reads landed by count rather than
> by strobe; the JTAG register readback slips below a `sync`/TCK ratio of about
> 4; and the negative control armed while the engine was already running.
>
> Re-measured with all four fixed, the DQS ceiling is **CK 140 at 238.9 MB/s
> read**, and **CK 180 fails in bulk with 4.7 M errors** — so "313.5 MB/s, DQS
> clean" is not merely unverified, it is wrong. `scripts/hyperram_ceiling.py`,
> and see #186/#188.

| PHY | CK | fabric `sync` | timing | read | errors |
|---|---|---|---|---|---|
| `HyperRAMPHY` (what the analyzer uses) | 120 | 120 | MET 135.9 | **198.2 MB/s** | 0 |
| `HyperRAMPHY` | 140 | 140 | MET 143.2 | **229.7 MB/s** | 0 |
| `HyperRAMPHY` | 150 | 150 | **FAIL 139.3** | — | — |
| `HyperRAMPHY` | 160 | 160 | **FAIL 147.7** | — | — |
| `HyperRAMPHY` | 180 | 180 | **FAIL 134.6** | — | — |
| DQS | 150 | 75 | MET 127.4 | 261.2 MB/s | 0 |
| DQS | 160 | 80 | MET 121.9 | 278.6 MB/s | 0 |
| DQS | **180** | **90** | MET 124.9 | **313.5 MB/s** | 0 |
| DQS | 200 | 100 | MET | — | 43,360,384 |

## The interesting part is not the MB/s

`HyperRAMPHY` clocks the **fabric** at CK, so CK 150 requires the whole design to
close at 150 MHz — and on this ECP5 it does not. It fails at 139.3, 147.7 and
134.6 MHz for CK 150, 160 and 180. That is not the HyperRAM refusing; it is
`nextpnr` refusing, and no amount of tuning the memory interface changes it.

`HyperRAMDQSPHY` clocks the fabric at CK/2. CK 180 therefore asks 90 MHz of the
fabric and closes with 35 MHz of margin.

**So DQS decouples the device clock from the fabric clock**, and the non-DQS path
cannot reach these rates on this silicon at any effort level. Two ways to read
the gain, both true:

* **1.58x** against what Cynthion ships today (CK 120, 198.2 MB/s)
* **1.37x** against the non-DQS PHY's own fabric-limited ceiling (CK 140, 229.7)

The failure at CK 200 is a cliff rather than a degradation — 43 M errors, with
die temperature flat at 30 C before and after, so it reads as a clock limit
rather than thermal.

We cannot yet tell you anything trustworthy about `BURSTDET`. Two of our own
DQS harnesses disagree: a streaming one reports it clear on every rung —
including rungs verifying 50 M words with zero errors — while our SoC build
reports thousands of assertions. Both latch it, both read it the same way, and
we have not resolved which is right. So `psram.py:779`'s TODO stands, and we are
not claiming otherwise.

## What we have

A port of the PHY to Amaranth 0.5 that produces the numbers above. It is close to
upstream's — same DQSBUFM / ODDRX2DQSB / IDDRX2DQA / TSHX2DQA arrangement, which
is the hard-won part — with the I/O layer rewritten and two things made
arguments rather than constants: `READCLKSEL` (upstream hardcodes `0b010` beside
a TODO) and the DQSBUFM read window's half-cycle phase. Both default to
upstream's values, and both accept a `Signal` if you want to sweep them at
runtime, which is how the table above was taken.

Pin polarity comes from the resource's own `invert`, so it is not specific to
our board — but the DQS pin-group constraint is. `DQSBUFM` serves one fixed
group and the strobe has to arrive on that group's designated pin, with no
routing to `DQSI`. On r1.4 that works out: RWDS is on D1, which prjtrellis tags
`LDQS8`, and all eight DQ lines are in `LDQ8`/`LDQSN8` — same group, same bank.
We have a small script that checks that against the device database rather than
assuming it, and it is worth running for any board before wiring this up.

One thing we cannot vouch for: we also drive this PHY from a Wishbone memory
window in a RISC-V SoC of our own, and that path reads one word late. It is our
own plumbing, unfinished, in code you would not run — mentioned only so it is
not a surprise later.

Happy to open a PR against luna for the PHY if that is wanted — say where you
would like it and we will shape it to fit. Equally happy for the three faults
above to be all you take from this.
