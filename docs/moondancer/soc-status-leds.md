# SoC status LEDs

Six FPGA LEDs on Cynthion r1.4, driven so that **a working board and a dead one do not
look the same**. Nothing was driving them before, which is a large part of why the silent
SoC took days: every failure mode looked identical from across the desk.

Pins are `E13 C13 B14 A15 D12 C11`, active-low, declared in the platform as
`LEDResources(..., invert=True)` so the gateware drives them active-high.

## The mapping

| # | colour | meaning | behaviour |
|---|---|---|---|
| 0 | **red** | **error** — any Wishbone bus error | solid, **latched** |
| 1 | orange | CPU has fetched at least one instruction | solid, latched |
| 2 | yellow | CPU has reached the I/O bus (third master alive) | solid, latched |
| 3 | **green** | **heartbeat** | **flashing**, ~1.4 Hz |
| 4 | blue | console data queued at least once | solid, latched |
| 5 | violet | USB connected and configured | follows `serial.connect` |

## Why latched, and why green flashes

**Everything except green is sticky.** These events are brief — a bus error is a single
cycle, a first instruction fetch happens once — and a human glancing at the board samples
at an arbitrary moment. A fault that blinks past unobserved is worse than one that stays
lit, so red latches: *a fault that cleared itself is still a fault*.

This is the same correction that made the sideband usable. Its `state` was wired to raw
Wishbone `cyc` strobes, high only during a transaction, and reading `0` was near-certain
even on a busy CPU. Latching turned "is a transaction in flight right now" into "has this
bus **ever** moved", which is the question actually being asked.

**Green flashes rather than sitting solid** because a stuck-high output and a healthy
design must not look the same. Motion proves the clock is running and the design is not
frozen — a solid LED proves only that a pin is high, which is also what a design held in
reset looks like.

## Reading the board

| what you see | what it means |
|---|---|
| green flashing, orange + yellow lit, blue lit, no red | working normally |
| green flashing, nothing else | clock runs, CPU is not fetching — check reset and the bitstream |
| green flashing, orange only | CPU fetches but never reaches I/O — the bus master or address map |
| orange + yellow but no blue | CPU runs and never writes the console — firmware, not gateware |
| **red lit** | a bus error occurred, whether or not anything else looks right |
| nothing at all | no clock, or no bitstream loaded |

The distinction in row 4 is exactly the one that was unavailable during the silent-SoC
investigation, and it is the one that would have pointed at the console immediately.

## Adding this to other designs

The heartbeat divider must be derived from the clock, not hardcoded — a design that raises
`sync` and leaves the count alone gets a heartbeat at the wrong rate, which is harmless,
but the same mistake in `SidebandDebug` gives a **dead** debug link rather than a slow one.
`vexii_hello_soc.py` derives both from `SYNC_MHZ` so they cannot drift. See #111.
