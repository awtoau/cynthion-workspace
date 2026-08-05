# PR 2 — apollo: a clock generator that can produce a `fast` domain at 2× `sync`

**Repo:** `greatscottgadgets/apollo` · **Branch:**
`awtoau/awto-apollo:variable-clock-fast-domain` · **Base:** upstream `main` ·
**Diff:** 1 file, +359 (new module) · **BLOCKING**

---

`luna`'s `HyperRAMDQSPHY` reads `ClockSignal("fast")` in every one of its
gearing primitives — `ODDRX2F`, `ODDRX2DQA`, `IDDRX2DQA`, `TSHX2DQA` and
`DQSBUFM` all take it as ECLK — and **no clock domain generator in either tree
produces one**. `LunaECP5DomainGenerator` offers 60/120/240 and always clocks
`sync` at 60.

That is the second half of why the DQS path has never run. The PHY does not
elaborate (separate PR), and even once it does there is nothing to clock it
from.

## Why this is a search rather than a call to `ecppll`

Three things:

**`ecppll` optimises its primary output** and lets the secondary land wherever
the resulting VCO puts it. Asking it for 80 MHz returns `usb` at 62.222 MHz —
3.7% out. That is a property of its heuristic, not of the hardware: the outputs
have independent dividers and choosing the VCO to serve both is a search
`ecppll` simply does not run.

**`usb` has no tolerance to speak of.** The ULPI PHY is fed a fixed 60 MHz over
a source-synchronous parallel interface — no start bit, no resynchronisation, no
mechanism to absorb a frequency error at all. Measured: a 90 MHz `sync` build
(`usb` 63.000, +5%) placed, packed and configured cleanly and then **never
appeared on the USB bus**, while a 100 MHz build — a *higher* clock, but `usb`
exactly 60.000 — enumerated immediately. The failure mode is a silently dead
device that looks exactly like "the CPU is too fast", and it sent one
investigation looking for a timing ceiling that was not there. This raises at
construction rather than warning.

**`fast` divides the same VCO as `sync`**, so it exists only where `CLKOP_DIV`
is itself divisible by the ratio. That is a restriction on which `sync`
frequencies are solvable at all, not a free extra output, and it is imposed
inside the search — a `sync`/`usb` pair that `CLKOS2` cannot serve would
otherwise fail after a place-and-route. Asking for `fast` and not getting it
raises rather than falling back to `ecppll`, because HyperRAM clocked from a
wrong `fast` corrupts data instead of failing to build.

## What it produces

Every rate the DQS measurements used, all with `usb` at exactly 60.000 MHz:

| sync | fast | VCO | CLKI | CLKFB | CLKOP | CLKOS | CLKOS2 |
|---|---|---|---|---|---|---|---|
| 60 | 120.0 | 480.0 | 1 | 1 | 8 | 8 | 4 |
| 75 | 150.0 | 600.0 | 4 | 5 | 8 | 10 | 4 |
| 80 | 160.0 | 480.0 | 3 | 4 | 6 | 8 | 3 |
| 90 | 180.0 | 540.0 | 2 | 3 | 6 | 9 | 3 |
| 100 | 200.0 | 600.0 | 3 | 5 | 6 | 10 | 3 |
| 110 | 220.0 | 660.0 | 6 | 11 | 6 | 11 | 3 |
| 120 | 240.0 | 480.0 | 1 | 2 | 4 | 8 | 2 |

80, 90 and 110 MHz were previously reported unreachable with a usable `usb`.
They are reachable.

## Shape

Portable: `_solve_both(sync_mhz, usb_mhz, input_mhz)` is arithmetic, and
`elaborate()` uses `platform.default_clk`. Imports nothing but `amaranth` and
the standard library.

New module, **imported by nothing yet** — deliberately separable from the PHY
work it enables, and reviewable on its own.

## One trap worth flagging

`FEEDBK_PATH` is `"CLKOP"`, so `CLKFB_DIV` counts *output* periods and
`VCO = input × CLKFB_DIV × CLKOP_DIV / CLKI_DIV`. Treating it as a plain VCO
multiplier is what made an earlier attempt produce every clock at twice the
requested rate, and got written off as "the dividers do not behave as
documented". They behave fine. Verified against `ecppll`, which returns
`CLKI_DIV=2 CLKFB_DIV=3 CLKOP_DIV=7` for 90 MHz: 60 × 3 × 7 / 2 = 630 MHz.

## Open question for the maintainers

`apollo` may not be its natural home. The consumer is `luna` gateware, and
`luna` already carries `LunaECP5DomainGenerator` in
`luna/gateware/architecture/car.py`. Placed here because that is where it was
written and where the flash-bridge speed ladder needed it; happy to move it to
`luna` beside the generator it generalises if that is preferred.

## Related

* greatscottgadgets/cynthion#147 — "Add DQS support for HyperRAM"
* The `HyperRAMDQSPHY` Amaranth 0.5 port, which is the other blocking half.
