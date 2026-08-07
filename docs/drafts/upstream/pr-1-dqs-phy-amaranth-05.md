# PR 1 — luna: port `HyperRAMDQSPHY` to Amaranth 0.5

> **WITHDRAWN — every DQS figure below was measured with faulty instruments.**
> The pattern used only the low 16 address bits and so repeated 64 times across
> the part; the controller ran luna's `HIGH_LATENCY_CLOCKS = 5`, below the
> minimum of 6 that CR0's 14 CK requires, so reads landed by count rather than
> by strobe; the JTAG register readback slips below a `sync`/TCK ratio of about
> 4; and the negative control armed while the engine was already running.
>
> Every throughput figure this project produced for the part is therefore
> void and has been deleted rather than annotated. No MB/s number is
> offered here; the re-measurement is outstanding. See
> `docs/chips/hyperram/bist-plan.md`.

**Repo:** `greatscottgadgets/luna` · **Branch:** `awtoau/awto-luna:dqs-phy-amaranth-0.5`
· **Base:** upstream `main` · **Diff:** 2 files, +31 −9 · **BLOCKING**

---

`HyperRAMDQSPHY` (added in #236, May 2024) cannot be instantiated on Amaranth
0.5. It reaches pads through the 0.4 record API, and the failure is at
construction, before any of the DQS logic runs — which is why this has never
been reported as a DQS problem, and probably why
greatscottgadgets/cynthion#147 has sat for two years.

Three distinct errors, depending on how `ram` is requested — reproduced against
a Cynthion r1.4 platform on Amaranth 0.5.9:

| request | error |
|---|---|
| `dir={'rwds':'-','dq':'-','cs':'-'}` — what `applets/hyperram_diagnostic.py` asks for | `TypeError: Object flipped(<Pin: Pin.Signature(1, dir='o'), ...>) cannot be converted to an Amaranth value` |
| plain `platform.request('ram')` | `AttributeError: 'Pin' object has no attribute 'io'` |
| everything raw | `TypeError: Object DifferentialPort(...) cannot be converted to an Amaranth value` |

## What the port does

`bus.rwds.io` and `bus.dq.io` reach a pad only on a raw request, and on 0.5
that is a `lib.io` port whose `.io` must be indexed.

`bus.clk` and `bus.cs` are the opposite case. They are ordinary output pins, the
`DELAYG` goes *before* the pin rather than onto it, so they want `.o` and a
normal buffered request. That also lets `clk` stay a `DiffPairs`, which it is on
every Cynthion revision from r0.1 — driving it raw would require the PHY to know
it is differential.

The manual inversion on CS goes with that. `i_D0=~self.phy.cs` was compensating
for a raw request bypassing the resource's polarity. With the pin buffered,
`PinsN` inverts, and inverting twice holds CS# asserted forever.
`HyperRAMPHY`, five hundred lines up, already drives `bus.cs.o` uninverted from
the same record — so this makes the two PHYs agree rather than introducing a
convention.

**On which applies to `~self.phy.cs`: the resource does.** Every Cynthion
revision declares CS# and RESET# with `PinsN`. The manual `~` was correct only
because the applet requested `cs` raw, and becomes wrong the moment the pin is
buffered.

And `RESET#` was never driven at all. `HyperBusDQSPHY` declares `reset`,
`elaborate()` never read it, so a design built on this PHY leaves the HyperRAM's
reset pin floating. It is driven from the record now; the applet's own
`ram_bus.reset.o.eq(0)` moves into the non-DQS branch so the two do not both
drive the pin.

The applet's `dir=` dict loses `'cs':'-'` to match.

## What the port does not do

Nothing to the `DQSBUFM` / `ODDRX2DQSB` / `IDDRX2DQA` / `TSHX2DQA` arrangement,
the `DDRDLL` settle sequence, the delay modes or the gearing. That part is
upstream's and is the hard-won half.

## Verification

Elaborating `HyperRAMDQSPHY` + `HyperRAMDQSInterface` against a Cynthion r1.4
platform on Amaranth 0.5.9 and emitting RTLIL now succeeds, with the primitive
set intact:

    1 DQSBUFM, 1 DDRDLLA, 1 ODDRX2DQSB, 1 TSHX2DQSA, 2 ODDRX2F,
    8 IDDRX2DQA, 8 ODDRX2DQA, 8 TSHX2DQA, 10 DELAYG

The applet's `DQS = True` branch elaborates too — the check that flag has never
had.

## Note

The PHY needs a `fast` domain at 2× `sync`; no clock domain generator in tree
produces one. That is now stated in the docstring, and is the subject of a
separate change.

## Measured, on r1.4, once this and a `fast` domain are in place

Same board, same harness, gateware pattern engine, 50 M words per rung:

| PHY | CK | fabric `sync` | nextpnr | read | errors |
|---|---|---|---|---|---|
| `HyperRAMPHY` (what the analyzer uses) | 120 | 120 | MET 135.9 | (withheld) | 0 |
| `HyperRAMPHY` | 140 | 140 | MET 143.2 | (withheld) | 0 |
| `HyperRAMPHY` | 150 | 150 | **FAIL 139.3** | — | — |
| `HyperRAMPHY` | 180 | 180 | **FAIL 134.6** | — | — |
| DQS | 160 | 80 | MET 121.9 | (withheld) | 0 |
| DQS | **180** | **90** | MET 124.9 | (withheld) | 0 |
| DQS | 200 | 100 | — | — | 43,360,384 |

The MB/s is not the interesting part. `HyperRAMPHY` clocks the **fabric** at CK,
so CK 150 requires the whole design to close at 150 MHz, and on this ECP5 it
does not — 139.3 and 134.6 MHz achieved for CK 150 and 180. That is nextpnr
refusing, not the HyperRAM, and no amount of tuning the memory interface changes
it. `HyperRAMDQSPHY` clocks the fabric at CK/2, so CK 180 asks 90 MHz of the
fabric and closes with margin.

**DQS decouples the device clock from the fabric clock.** That is what this
unlocks; the throughput follows from it.

The failure at CK 200 is a cliff rather than a degradation, with die temperature
flat at 30 °C before and after, so it reads as a clock limit rather than
thermal. `BURSTDET` asserts throughout — the ECP5's own report that the read
window is aligned.

## Scope

Not claimed: that DQS is integrated end to end. We also drive this PHY from a
Wishbone memory window in a RISC-V SoC of our own and that path reads one word
late; that is our own unfinished plumbing, in code you would not run, not a
property of the PHY. The streaming configuration measured above is the one
`analyzer/fifo.py` uses.

## Related

* greatscottgadgets/cynthion#147 — "Add DQS support for HyperRAM"
* #236 — added `HyperRAMDQSPHY`

One further thing worth doing that this PR does not: `HyperRAMDQSPHY` keeps
`DDRDLLA`'s LOCK and the end of its PAUSE sequence internal. The entire read
path's delay codes are invalid until that DLL locks, and nothing above the PHY
can tell — a design reporting a clean read with the DLL unlocked has measured
nothing. Happy to add that if wanted.
