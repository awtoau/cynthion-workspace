# PR 6 — cynthion: the HyperRAM clock ceiling harness

**Repo:** `greatscottgadgets/cynthion` · **Branch:**
`awtoau/awto-cynthion:hyperram-ceiling-harness` · **Base:** upstream `main` ·
**Diff:** 4 new files under `contrib/hyperram-ceiling/`, +1244 · not blocking

---

The measurements quoted on #147 should be reproducible rather than taken on
trust. This is the harness that produced them.

```
./ceiling.py --build   # bitstreams only; the board is not involved
./ceiling.py --run     # board only, uses what is already built
./ceiling.py           # both
```

Sustained write / read / verify with a gateware pattern engine and no CPU in the
loop, over a ladder of **device CK** rungs, with both PHYs built for each rung.

## Why the x-axis is CK

| PHY | gearing | bits per `sync` cycle | device CK |
|---|---|---|---|
| `HyperRAMPHY` | `ODDRX1F`, 2:1 | 16 | `sync` |
| `HyperRAMDQSPHY` | `ODDRX2F`, 4:1 | 32 | **2 × `sync`** |

A sweep indexed by `sync` would compare a part running at 120 MHz against one
running at 240 and call it a PHY comparison. Each rung is a CK; non-DQS is built
at `sync = CK`, DQS at `sync = CK/2`.

That difference is the finding, not the throughput: `HyperRAMPHY` puts the
*fabric* at CK, so raising CK raises what the whole design must close at.

## What makes a rung's verdict evidence

Every recorded trap on this interface produced a plausible wrong answer rather
than a failure, so "it passed" needs support. All of this is in the harness
rather than in the reader's trust:

* **A negative control on every rung.** Reads are checked against the complement
  of what was written — a value the part cannot return — so a working comparator
  must report every word wrong. Without it, zero errors is equally consistent
  with a comparator that never fires, and this sweep found no failure to
  demonstrate the detector on. (Slack of exactly one burst: the control is armed
  while the engine is running, so the words in flight at that moment correctly
  match. Measured on the harness's first run: 78 matched of 6,871,936, against a
  128-word burst — a boundary, not a broken control.)
* **`BURSTDET`**, latched from `DQSBUFM`. With fixed latency set in CR0 a read
  can come back clean because the count landed right rather than because the
  strobe was found. A DQS rung passing with `BURSTDET` clear has demonstrated
  nothing about DQS.
* **An address-derived pattern.** A controller that stopped advancing its
  address would return one word forever, and a constant fill would score that as
  perfect.
* **Every mismatch counted, and the first one kept** with its index, what
  arrived and what was due. One bad word in ten million and ten million bad
  words are different faults, and *how* a value is wrong separates a half-word
  slip from a dead lane from noise.
* **Die temperature** before and after, so a rung that failed while the part was
  hotter than the rung below is not read as a clock limit.
* **The bitstream states the clock it was built for**, and the host refuses to
  measure if that disagrees with its own idea — a rate computed from a requested
  frequency that was not achieved is wrong by exactly the rounding error.
* **Counters compared across a window**, so the rate is measured over a known
  number of words rather than over "since configuration".

## tCSM caps the burst

`CR1[1:0] = 01b` is a 4 µs tCSM — the longest CS# may stay low. Distributed
refresh cannot run while CS# is low, so a longer burst is not slow, it is
illegal, and it fails by forgetting later rather than by returning anything
wrong at the time. `BURST_WORDS = 128` is 2.13 µs at the slowest clock swept and
less above it, so every transaction is legal and the throughput reported is one
the part can hold.

For contrast: an earlier internal test moved 2048 words in one transaction — 17
µs at 120 MHz, over four times tCSM — and its headline figure is therefore a
rate the part is not specified to sustain.

## Placement

Self-contained under `contrib/`, importing nothing from the `cynthion` package
but the r1.4 platform. It touches no shipping code and is easy to drop if you
would rather it lived elsewhere or nowhere.

## Depends on

* the `HyperRAMDQSPHY` Amaranth 0.5 port (luna) — otherwise the DQS half cannot
  elaborate;
* `readclksel` as a constructor parameter (luna) — the phase sweep drives it
  from a JTAG register.

Verified by elaborating both paths against a Cynthion r1.4 platform on Amaranth
0.5.9 and emitting RTLIL: non-DQS at `sync` 120 (CK 120), DQS at `sync` 90
(CK 180, one `DQSBUFM`).

## Known gap

`HyperRAMDQSPHY` keeps `DDRDLLA`'s LOCK and the end of its PAUSE sequence
internal, so the two status bits meant to carry them read as constant 1 and are
placeholders rather than evidence. The whole read path's delay codes are invalid
until that DLL locks and nothing above the PHY can tell — a design reporting a
clean read with the DLL unlocked has measured nothing. Our own version of the
PHY exposes them; bringing that upstream would close this.

## Related

* #147 — "Add DQS support for HyperRAM"
