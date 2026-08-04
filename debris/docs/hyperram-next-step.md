# HyperRAM: the window is built, and nothing on the CPU uses it

**Retired 2026-08-05.** The durable half is in
[`docs/chips/w956a8-hyperram.md`](../../docs/chips/w956a8-hyperram.md); the remaining
work is #90.

State as of 2026-08-03T13:50+10:00. Written because the work is finished in
gateware and proven in simulation, and a reader looking at the benchmark numbers
would reasonably conclude the opposite.

## HyperRAM already has Wishbone

It is not a missing peripheral, and it does not need one added.

- `HyperRAMWishbone` — `ecp5-test/riscv/vexii_bootram.py:66`. A 32-bit memory
  window with a `MemoryMap` resource `("memory","hyperram")`.
- Mapped at `HYPERRAM_BASE = 0x20000000`, 8 MiB —
  `ecp5-test/riscv/vexii_hello_soc.py:324-325`, added to the decoder at `:889`.
- Declared `main=1, exe=1` in the CPU's PMA list (`vexii_hello_soc.py:540`).
  That flag is what makes it cached and executable rather than routed to
  `iobus` one transaction at a time.
- Burst coalescing: `cti == INCR_BURST && bte == LINEAR` holds one HyperBus
  transaction open across beats — `vexii_bootram.py:126-130`, `:145-168`. Capped
  at `HYPERRAM_MAX_BURST_WORDS = 748` (`:60-63`) to stay inside the part's
  768-CK tCSM refresh deadline. **Off on this SoC** (`sustained=False`): a
  HyperBus data phase cannot be stalled, and `RegisteredResponse` makes the CPU
  a beat slower per beat than one consumes words. Holding a transaction open
  under that deficit corrupted reads and writes alike — see below.

The whole SoC is amaranth-soc Wishbone: one flat 30-bit decoder at
`vexii_hello_soc.py:574-576`, with VexiiRiscv emitting three native Wishbone
masters (`ibus`/`dbus`/`iobus`, `vexii_cpu.py:240-265`) arbitrated at `:940-946`.
There is no second bus standard in the design.

`scripts/soc_hyperram_sim.py` measures the result against a master that supplies
a beat every two cycles: a 64-byte cache-line refill is **1 HyperBus transaction
/ 49 CK**, against the classic arrangement's **16 transactions / 304 CK**. Both
are asserted, the classic path kept as a negative control. (These read 51 and 336
until the model's data phase was corrected — it entered two cycles late, which
reads tolerated because RWDS gates the controller's sampling. 17 CK of overhead
per transaction is what the controller's states actually count: 3 command words
+ 13 latency + 1 recovery.)

**No master in this SoC supplies a beat every two cycles**, which is why that
figure is a property of the coalescing logic and not of the board. `hr cross`
found the consequence: `8/16 correct, bad 1010101010101010`, every odd beat's
halves transposed, reproduced exactly in section 9 once `RegisteredResponse` was
put in the sim's path.

## The gap

**No firmware reaches HyperRAM through that window.** The benchmark still uses
the CSR staging port, and says so at `firmware/cynthion-soc/src/bench.rs:9`:

    hyperram   CSR port      main=0, never   read/write, seq/random

with the accesses at `:340-359` going through `hyperram::seek_word()` /
`read_word()`. So every published HyperRAM number describes the staging port,
and the 51-CK refill is currently dead code from the CPU's point of view.

## The next step

Move `bench.rs` (and `hyperram.rs`'s read/write path) onto `0x20000000` and
re-benchmark. That is what turns the work already merged into a number, and it
is the only thing standing between the current state and closing issue #90.

Keep the CSR port. It is not redundant: it is how firmware stages an image into
HyperRAM before rebooting into it, and it works before caches are meaningful.
The two paths answer different questions, so the benchmark should report both.

## Related, and already resolved

The CSR register offsets in `firmware/cynthion-soc/src/hyperram.rs` were stale by
four registers (`0x09/0x0a/0x0b/0x0c` against the gateware's
`0x0c/0x0d/0x0e/0x10`) — firmware polled `valid` from a byte inside `addr_rd`.
Fixed in `f0abbb6`, then superseded properly: the constants now come from
`cynthion_soc_pac::bootram::offset` (`hyperram.rs:169`, `:181-186`), generated
from the SoC's own memory map. `soc_generate_pac.py --check` now verifies
committed register offsets as well as bases — 78 checked. The hand-written
constants that allowed the drift are gone.

## Suggested order after this

1. The `bench.rs` move above.
2. Flash: #89 (parked at 48 MHz quad) and #100 (achievable ceiling). Prior work
   found no read ceiling below 144 MHz SCK in five modes, so the remaining gap
   is integration, not the part.
3. Hardware runs for what is only sim-proven — #132 and #116 carry
   `needs-hardware-test`.
4. #92: exercise the DQS PHY from a RISC-V core rather than a standalone ceiling
   bitstream.

## Unrelated: upstream/GSG work is parked, nothing submitted

`scripts/upstream_ci.py` runs a GSG repo's own CI in a disposable clone under
`tmp/`; `docs/upstream-ci-workflow.md` has the detail and the measured findings.
The short version is that upstream's automated signal is thinner than it looks —
LUNA's entire Actions gate is 93 tests in 2.2 s, and PR #301 passes it
identically with and without the fix it exists to make. **No fork was pushed and
nothing was submitted anywhere.**
