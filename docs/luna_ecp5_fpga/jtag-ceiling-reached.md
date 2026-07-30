# JTAG configuration: the path is done, the time is in USB

Two proposals — transmit-only DMA, and raising SCK to 24 MHz — were investigated
and **neither was implemented**, because measurement showed both would be
no-ops or unsafe. This records why, since both looked well-founded.

## There is no CPU cost left for DMA to remove

The "0.455 µs/byte CPU term" that justified TX-only DMA is an artifact of the
same mistake that produced the earlier 3.93 µs/byte figure: dividing a whole
`SCAN` call by its payload, which charges the fixed ~145 µs USB round trip to
per-byte clocking.

Measuring the **marginal** cost instead — differencing 64-bit and 2048-bit scans
over the same round trip, so the fixed cost cancels — gives **0.663 / 0.684 /
0.715 µs/byte** across three runs against a **0.667 µs/byte** wire floor.

That is **2-3% overhead, not 41%**. The pipelined `spi_send()` already took the
win by keeping the shifter continuously fed.

This independently reproduces why the earlier DMA attempt was archived, and it
also revises the synthetic-benchmark conclusion that the path was CPU-bound with
1.11 µs/byte of overhead. Same artifact, measured two different ways.

## SCK is already at the ceiling, and cannot be swept

`f_SCK = 48 MHz / (2 x (BAUD+1))`, so the divider steps are:

| BAUD | SCK | period |
|---|---|---|
| 0 | 24.0 MHz | 41.7 ns |
| **1** | **12.0 MHz** | **83.3 ns** (current) |
| 2 | 8.0 MHz | 125 ns |

**There is no setting between 12 and 24 MHz.** The requested sweep cannot be
run; it is one binary step.

And the ceiling is the SAMD11, not the ECP5. Datasheet Table 35-50 gives `tSCK`
**min 84 ns — an 11.9 MHz rated maximum** in master mode. So BAUD=1 is already
marginally past rated and BAUD=0 would be 2x past.

Separately, `tMIS` (MISO setup) is 21 ns typical against a 20.8 ns half-period
at 24 MHz, so **duplex readback cannot validate 24 MHz even in principle** —
which removes the instrument that would have proved the wire still worked.

## Where the time actually is

Profiling a verified configure (`DONE=1`, `BSE_ERR=0`):

| | |
|---|---|
| end-to-end | 953-1017 ms |
| bulk shift | 679 ms |
| **of which wire time** | **67 ms** |
| **USB per-chunk overhead** | **612 ms (90% of the shift)** |

394 chunks, two control transfers each. **JTAG clocking is about 7% of
end-to-end.** Zeroing it entirely would take ~1000 ms to ~940 ms.

The levers that would matter are **chunk size** — capped by
`jtag_out_buffer[256]` and RAM-bound at 2728 of 4096 bytes — and **collapsing
`SET_OUT_BUFFER` + `SCAN` into one transfer**.

## The TX-only argument was correct, and still not worth doing

The reasoning holds and was verified: the ECP5 self-validates via
`LSC_RESET_CRC`, BSE code 3 is `CRC check failed`, and the host already
suppresses `GET_IN_BUFFER` for the burst. **Dropping TDO loses no error
detection.**

It would buy roughly **2 ms of ~950 ms**, which does not justify the
silent-failure surface it creates.

The stricter verification it implied was adopted anyway: every configure now
decodes `DONE`, `FAIL` and the BSE code explicitly rather than relying on an
exception not being raised.

## The standard test, and why there was not one

Its absence was actively misleading, so this is now the only figure worth quoting.

These docs contained roughly **forty distinct millisecond figures** for
configuration timing with no way to tell which measured the same work. All
honestly recorded, none comparable: different bitstreams, different firmware,
different scopes -- whole configure versus shift path versus a single `SCAN` call.

Two errors that followed from that, both mine:

- I compared 890 ms against a recorded 1680 ms and reported **1.89x**. The two came
  from different bitstreams. Same payload the figures were 846 and 774 ms, a 1.09x
  change reported as nearly double.
- The `2575 -> 1680 ms` above and the `953-1017 ms` in the section below are both
  labelled configure time. Both are correct. They measure different payloads on
  different firmware.

**`scripts/jtag_fixed_benchmark.py`, committed payload of 122880 bytes.** Fixed
rather than a real bitstream because `top.bit` changes size whenever the gateware
does, so a "faster" result can be a smaller bitstream. The size sits mid-range of
the real bitstreams here (103364 to 136184 bytes) and divides evenly by
128/256/512/1024, so no chunk size gets a short final transfer the others do not.

Scope is the **shift path only**: no `ISC_ENABLE`, no CRC check, no DONE poll, TAP
left in SHIFT-DR. The FPGA ignores the data, so its configuration survives and the
benchmark is safe to repeat against a board that is doing something else. The
absolute number is correspondingly lower than a real configure.

| chunk | chunks | best | throughput | spread |
|---|---|---|---|---|
| 128 B | 960 | 667.6 ms | 184.1 KB/s | 1.8 ms |
| 256 B | 480 | 558.8 ms | 219.9 KB/s | 3.3 ms |
| **512 B** | **240** | **488.9 ms** | **251.3 KB/s** | 3.1 ms |

Spread of 1.8-3.3 ms against 4-15 ms for real-bitstream timing. That matters: the
remaining levers are expected to save less than the chunk change did, so the
instrument has to resolve a few milliseconds.

## The 1.53x claim, restated on the standard test: it is 1.13x

`scripts/jtag_speed_bisect.py` reflashes historical firmware and benchmarks each
commit, so claims made before the standard test existed get real numbers rather
than being taken on trust.

Only four commits between the stock `v1.1.1` tag and HEAD touch the JTAG or SPI
path, so this is five direct measurements rather than a search. All at 256 B/chunk,
because 512 needs `GET_INFO`, which only HEAD implements -- on older firmware the
host silently falls back to 256, so a "512" row would read as no change.

One table, both chunk sizes, on the 122880-byte payload throughout.

| commit / limit | what | 256 B | 512 B | of theoretical |
|---|---|---|---|---|
| **`v1.1.1`** | **stock release** | **713.9 ms** | n/a | 11.5% |
| `4bf7691` | enable LTO | 639.1 ms | n/a | 12.8% |
| **`e034daa`** | **pipelined `spi_send` + suppress TDO readback** | **566.9 ms** | n/a | 14.4% |
| `0e9bfb1` | `JTAG_BUFFER_SIZE` define, no functional change | 562.1 ms | n/a | 14.6% |
| **`HEAD`** | **512-byte buffers + `GET_INFO`** | **555.4 ms** | **488.9 ms** | 14.7% / **16.8%** |
| | | | | |
| **no USB payload** | `0xb9`, pattern generated in firmware | **275 ms** | **137 ms** | 30% / **60%** |
| **theoretical wire** | 12 MHz SCK, 1 bit per clock | 81.9 ms | 81.9 ms | 100% |

Cumulative against stock, at 256 B: 1.00x, 1.12x, 1.26x, 1.27x, **1.29x**. Stock to
HEAD using HEAD's own 512-byte chunk: **1.46x**.

**Every `n/a` is an impossibility, not a gap.** Only HEAD has 512-byte buffers.
Everything before it declares `jtag_out_buffer[256]` and stalls anything larger --
`if (request->wLength > sizeof(jtag_out_buffer)) return false;` -- so a 512-byte
request is refused by the firmware itself. The host would also fall back to 256
regardless, since `GET_INFO` arrived with HEAD; the benchmark detects that and
declines to label a 256-byte run as 512, which is what stops an unimplemented
`GET_INFO` from flattering a result.

Theoretical is chunk-independent by definition: 122880 x 8 / 12 MHz = 81.9 ms,
1500 KB/s. Chunking is a transport concern and the wire does not see it.

**Read the columns, not the diagonal.** At 256 B, HEAD spends 280 ms of its 555 in
USB against 275 ms clocking -- half and half. At 512 B it is 352 of 489 in USB against
137 clocking -- 72/28. The ratio moves because doubling the chunk **halves** the
firmware-side cost (275 -> 137) while barely touching USB (280 -> 352, and that is
worse in absolute terms).

That last point is the one the single-column version hid: **going to 512 bytes made
the USB portion bigger, not smaller.** It won overall because the firmware-side saving
was larger than the USB-side loss. Which is also why the chunk change bought less than
arithmetic predicted -- it was fixing the smaller half, and slightly worsening the
larger one.

## USB transfers already use DMA, and that reframes the levers

Asked whether USB transfers use DMA. **They do**, and the answer matters more than
expected.

The SAMD USB peripheral **is** a DMA engine. `dcd_edpt_xfer()` at
`dcd_samd.c:276` does:

    bank->ADDR.reg = (uint32_t) buffer;

so the caller's buffer address goes straight to the hardware, which moves the bytes
itself. Zero copy, no CPU involvement. That is also what `sram_registers[8][2]` is --
the endpoint descriptor table the hardware reads to find those addresses, which is
why it is silicon-mandated and not reclaimable.

**So there is no CPU-side data-movement cost to remove on the USB side either.** This
is the same conclusion the DMA investigation reached for the SPI side, arrived at from
the opposite direction: both halves of the transport are already hardware-driven.

### Where the 1466 us per chunk actually goes

At 512 B the USB portion is 351.9 ms over 240 chunks, so **1466 us per chunk**.
Decomposing what is known:

| component | per chunk | how known |
|---|---|---|
| two control transfers, fixed cost | 428 us | 2 x 213.9 us measured |
| 512 bytes of payload at 12 Mbit/s | 341 us | arithmetic |
| **unaccounted** | **~700 us** | |

**Half the USB cost is neither request overhead nor data movement.** That is the
single most useful thing in this section, because both remaining levers target the
428 us: the collapse removes half of it, and double-buffering hides clocking behind
it. Neither touches the 700 us.

Candidates for the 700 us, none yet established:

- **Frame scheduling.** Full-speed USB schedules control transfers per 1 ms frame,
  and a transfer with a data stage plus a status stage may not complete within one
  frame. If each chunk costs a whole frame boundary somewhere, that alone is
  hundreds of microseconds and is a property of the bus rather than of either end.
- **Host-side per-call cost** in libusb or the kernel, above the 213.9 us measured
  for a near-empty transfer -- the measurement used a 32-byte reply, so a 512-byte
  data stage may cost more than the payload arithmetic suggests.
- **`tud_task()` latency**: the firmware processes the transfer from the main loop,
  not from the ISR, so completion waits on a loop iteration.

**This should be measured before either lever is built.** If the 700 us is frame
scheduling then the collapse is worth its 51 ms and double-buffering is worth its
137 ms, and both leave 168 ms of frame cost untouched -- which would make the real
ceiling around 320 ms rather than the 352 ms the USB portion implies. Timing a single
`SET_OUT_BUFFER` of 512 bytes in isolation, against one of 8 bytes, would separate
payload from fixed cost directly.

## 1024-byte chunks DO fit, if JTAG may claim what is idle

I previously said the buffer merge saves only 256 bytes and that 1024-byte chunks
therefore do not fit. **That was wrong** -- it counted only the console ring and
missed everything else that is idle during a JTAG session.

The reasoning that makes the rest claimable: **during JTAG programming everything
else is off, and the board is rebooted afterwards.** So any buffer private to a
subsystem that is gated off is on the table, and its contents need not survive.

| source | bytes | why claimable |
|---|---|---|
| already unallocated | 644 | free today |
| `uart_rx_ring` | 256 | `console_task()` returns immediately while the JTAG lock is held |
| `_cdcd_itf` | 296 | TinyUSB CDC class state -- the console is gated off, and CDC is not the JTAG path |
| stack reservation | 336 | 1024 reserved, **344 measured** high-water; keeping 2x margin at 688 still frees 336 |
| **total** | **1532** | |

**Attempted, and it does not fit.** This was tried and reverted -- the arithmetic
above is wrong in a way worth recording, because it is an easy mistake to repeat.

Setting `JTAG_BUFFER_SIZE` to 1024 puts RAM at **95.12%**, and the budget check in
`check.py` rejected it. The error: the reclaimed bytes were counted correctly, but
doubling the buffer costs **both** of them -- `jtag_in` and `jtag_out` -- so the price
is 1024 bytes, not the 512 the "one more doubling" framing suggests. And the union
cannot absorb it, since the console ring is 256 bytes against a JTAG pair that would
be 2048.

Working backwards from the 85% ceiling: non-union `.bss` is 1144 bytes and the stack
is 704, so the union may be at most about 1634 bytes -- **a JTAG pair of roughly
2 x 817.** 512 stands; 1024 does not.

To actually reach 1024-byte chunks, one of these would have to happen first:

- **Drop `jtag_in_buffer` during writes.** It is the larger half of the pair and
  configure never reads TDO -- `ecp5.py` passes `ignore_response=True`. But
  `spi_send()` dereferences its receive pointer unconditionally, so this needs a
  NULL-receive path in the SPI driver first.
- **Find another 400+ bytes.** `_cdcd_itf` at 296 is the only remaining candidate of
  size, and it is TinyUSB's internal state with the CDC port possibly still open
  during JTAG -- the riskiest claim available and still not enough alone.

So the request-count lever is exhausted at 512 bytes on this part, and the ~51 ms it
would have bought is not available without one of the above.

That is worth roughly **51 ms** by the request-count arithmetic -- 120 chunks instead
of 240, at 213.9 us of fixed cost per request -- which is the same as the collapse, for
no protocol change.

### What each claim actually requires

They are not equally easy, and the order matters:

**The stack claim is the safest and needs no code**, only a linker flag. The script
already reads `STACK_SIZE = DEFINED (STACK_SIZE) ? STACK_SIZE : ... : 0x400`, so
`-Wl,--defsym=STACK_SIZE=0x2b0` sets 688 bytes with no linker-script edit. It rests
entirely on the measurement being trustworthy, which is why the paint-and-measure work
came first: 344 bytes is a **lower bound**, since depth already in use when painting
ran is invisible and one run does not prove a worst case. 2x margin is the hedge.

**The ring claim is a union**, with the exclusivity proven by the JTAG lock spanning
`jtag_init()` to `jtag_deinit()` rather than by convention.

**The `_cdcd_itf` claim is the awkward one and probably should not be taken.** It is
TinyUSB's internal state, not ours: reusing it means either patching TinyUSB or
aliasing a structure whose layout is not ours to rely on. And CDC is not merely idle
during JTAG -- the host may still have the port open, so TinyUSB could touch that
state on an unrelated control transfer. Without it the total is 1236, which still
covers the 1024 needed.

So: **1024-byte chunks fit using the unallocated space, the ring, and the stack
reduction alone.** `_cdcd_itf` is not needed and carries the most risk.

## The two remaining levers, quantified before building either

Both measured rather than estimated, because the chunk change taught that arithmetic
here overpredicts.

| lever | ceiling | what it needs |
|---|---|---|
| collapse `SET_OUT_BUFFER` + `SCAN` | **51.3 ms** | one new vendor request, host fallback |
| double-buffer fill against clocking | **137 ms** | both ends restructured, async host |

**Collapse: 51.3 ms.** A bare control transfer that does almost nothing --
`GET_ID`, 500 samples -- costs **213.9 us**. That independently reproduces the ~215 us
figure from the decomposition above. At 240 chunks, removing one of the two requests
per chunk is 240 x 213.9 us = 51.3 ms, or 10.5% of the 488.9 ms total.

**Double-buffering: 137 ms, and only with perfect overlap.** The split at 512 B is
351.9 ms USB against 137 ms clocking, so overlapping them perfectly gives
`max(351.9, 137) = 351.9 ms` -- a saving of exactly the clocking time, 28% of total.
It cannot do better than that: USB is the longer leg, so the clocking hides inside it
and the USB time remains exposed.

### Why double-buffering is not a small change

The path is synchronous at three levels, and all three have to change:

- **`handle_jtag_request_scan` clocks then acknowledges.** It calls `jtag_scan()`,
  which shifts every byte, and only then completes the control transfer. So the host
  cannot learn the scan finished without waiting for it, and the firmware has no state
  in which "scan in progress, next buffer accepted" exists.
- **The host loop blocks.** `_scan_data_chunk` issues `SET_OUT_BUFFER` then `SCAN` and
  waits, so chunk N+1's fill cannot begin until chunk N's clocking returns.
- **There is one buffer.** `jtag_out_buffer` is written by USB and read by the SERCOM
  with nothing between them, so a second fill would corrupt an in-flight scan.

So it needs a second buffer, a non-blocking `SCAN` with completion reporting, and an
async host loop -- against 137 ms of ceiling on a 489 ms operation.

**The collapse is the better first move** despite the smaller number: one vendor
request, no restructuring, and it composes with double-buffering later rather than
being replaced by it.

## Where the remaining time goes

### The gap is the whole story

At 512 bytes the real path takes **488.9 ms** where the same clocking with no USB
payload takes **137 ms**. So roughly **352 ms, 72% of the total, is USB transport**,
and only 137 ms is the microcontroller clocking bits.

And the no-USB figure is itself only 60% of theoretical, so there are two distinct
gaps:

- **81.9 -> 137 ms** is the SERCOM and its loop: 55 ms of per-byte overhead the wire
  does not account for. The marginal-cost differencing above puts clocking at
  0.663-0.715 us/byte against a 0.667 us/byte wire floor, which is 2-3% -- so most
  of this 55 ms is per-*chunk* cost (pinmux, setup, the verify loop), not per-byte.
- **137 -> 488.9 ms** is USB. This is the part the two untried levers attack.

Note the no-USB figure halves cleanly from 256 to 512 bytes (275 -> 137 ms) while the
real path improves only 12% (558.8 -> 488.9). That asymmetry is the clearest single
statement of where the time goes: doubling the chunk halves the firmware-side
per-chunk cost, and barely dents the USB cost.

### Two caveats on the 0xb9 numbers

**`blocks` in the reply is in 256-byte units, not bytes** -- `blocks = chunk *
repeats / 256` at `jtag.c:337`. Reading it as bytes doubles the apparent payload,
which it did on the first attempt here.

**The mismatch counter only means anything in BYPASS.** The benchmark expects TDO to
be TDI delayed by one bit, which holds in SHIFT-DR with BYPASS selected. Run from a
context that leaves the TAP elsewhere and it reports mismatches on every byte -- as
it did here. That is the harness being used wrongly, not a data-integrity failure,
and the counter is worthless unless the TAP state is set up deliberately.

Verified behaviourally rather than by version string, which is worth noting: the
build reports `v1.1.1-41-gbb82d39-dirty` even when the code is stock, because the
version comes from `git describe` on the working tree rather than from the checked-out
firmware. Confirmed stock instead by both project-added vendor requests stalling --
`GET_INFO` (0xb8) and `GET_STACK_USAGE` (0xa5).

Stock builds at **94.17% ROM / 86.52% RAM**, which is why it needed no LTO to fit
its own feature set and why ours does.

**So `e034daa` is 639 -> 567 ms, or 1.13x -- not the 1.53x recorded at the time.**
That original figure was a whole-configure measurement on a different payload, and
it is not wrong so much as not comparable. On identical work the change is real and
about a quarter the size claimed.

The `0e9bfb1` row is the useful control: a pure refactor, and it moves 4.8 ms, which
sets the noise floor for reading the others.

Getting stock to build at all took two fixes, both artifacts of mixing eras rather
than facts about stock. `SOURCES` is a wildcard over `src/*.c`, so **four** files this
project added stay in the build after an older checkout -- they are untracked at
`v1.1.1`, so git leaves them alone: `stack_probe.c/h` and `apollo_mode.c/h`. With
those still present, stock fails on `-Werror=array-bounds` and then at 107% ROM /
103% RAM. The script now sets all four aside per commit.

## The synthetic benchmark (0xb9), and the floor that was not a floor

Merged from a separate document, because two files about JTAG timing is how these
docs came to hold forty non-comparable figures in the first place.

Vendor request `0xb9` (`handle_jtag_benchmark`) exists to measure clocking with **no
bulk USB traffic at all**: it generates its own pattern in firmware
(`jtag_out_buffer[i] = i * 7 + 1`), clocks it, verifies TDO, and returns elapsed
milliseconds. It also accepts a SERCOM divider, so SCK can be swept with nothing in
the way to confound the result.

### What it originally concluded, and why that was wrong

Sweeping the divider with every readback bit-exact gave:

    time per byte = 8/SCK + 1.11 us

The second term was constant across an 8x SCK range, which was read as: the wire
needs 0.667 us/byte at 12 MHz, so the polled loop was spending **1.11 us/byte of CPU
on top**, and the path was CPU-bound rather than wire-bound.

**That was an artifact**, and the same one that produced the 3.93 us/byte figure
elsewhere: dividing a whole call by its payload charges the fixed per-call cost to
per-byte clocking. Measuring the *marginal* cost instead -- differencing 64-bit
against 2048-bit scans so the fixed cost cancels -- gives 0.663-0.715 us/byte against
a 0.667 us/byte wire floor. **2-3% overhead, not 166%.**

So `spi_send()`'s pipelining did take a real win, but the "1.11 us/byte of CPU" it
was credited with removing never existed as a per-byte cost. It was per-chunk cost,
mis-attributed.

### Two false results its own controls caught

Worth keeping, because both looked clean:

- The DMA and baseline firmwares measured **identically** on the first isolation
  attempt -- 329.7 against 329.1 us. A positive control asserting that `SCAN` is
  accepted *and* that its cost scales with bit count is what surfaced it: a stalled
  or no-op request looks exactly like a fast one on a stopwatch.
- The first harness used **wrong request numbers** -- `0xb5` is `GOTO_STATE`, not
  `SCAN`. Checking against the firmware enum rather than trusting plausible timings
  is what caught that.

### Using it correctly

Two traps, both hit while collecting the numbers in the table above:

- **`blocks` in the reply is in 256-byte units**, not bytes -- `blocks = chunk *
  repeats / 256` at `jtag.c:337`. Reading it as bytes doubles the apparent payload.
- **The mismatch counter is only meaningful in BYPASS**, where TDO is TDI delayed one
  bit. Called from a context that leaves the TAP elsewhere, it reports a mismatch on
  every byte -- harness misuse, not corrupted data.

## Things tried that did not work

Recorded so they are not retried, and because the failures were more informative than
some of the successes.

| attempt | outcome | why |
|---|---|---|
| `JTAG_BUFFER_SIZE` 512 -> 1024 | **rejected at 95.12% RAM** | doubling costs BOTH halves of the pair, 1024 bytes not 512 |
| SCK 12 -> 24 MHz | **rejected, unsafe** | divider steps 8/12/24 with nothing between; SAMD11 `tSCK` min 84 ns = 11.9 MHz rated, so 12 is already past |
| SERCOM DMA | **implemented, marginally slower** | 1711-1751 ms against 1698-1715 polled; no CPU cost left to remove |
| TX-only (drop TDO entirely) | correct but not worth it | ~2 ms of 950, for a silent-failure surface |
| bulk streaming | **built, worked, no faster** | 1703 vs 1683 ms; its stated cause was later disproven, so still unexplained |
| `-fstack-usage` for stack depth | **wrong tool** | LTO inlines across units, so per-function frames stop matching the final binary |
| `git bisect` across apollo history | **failed on all 5 points** | checking out old apollo replaces `apollo_fpga/`, removing `boot_to_dfu()` -- one of our own additions |
| paint-and-measure, first version | **self-contradictory** | LTO resolved `&_sstack` differently per inlined copy; reported full-region use AND no overflow |
| word-wise stack scan | **latent bug** | one coincidental `0xDEADBEEF` truncates the scan and understates usage |

Four of these are worth more than a note.

**The 1024-byte buffer is the one to not retry.** The reclaimed RAM was counted
correctly -- 320 from the stack, 256 from the union, 644 already free -- but a doubling
buys `jtag_in` and `jtag_out` together, so it costs 1024. Working back from the 85%
ceiling: non-union `.bss` is 1144 and the stack 704, so the union caps near 1634 bytes,
a JTAG pair of about **2 x 817**. The only route left is dropping the receive buffer for
writes, which `spi_send()` now permits.

**The bisect failure is a trap worth naming.** `boot_to_dfu()` is this project's
addition and is absent from stock `v1.1.1`, so checking out an old commit removes the
method needed to flash it. Every point failed with `AttributeError` before touching the
board, and the run left it in the bootloader. Fixed by checking out `firmware/` only,
which also keeps the host library constant and removes it as a variable.

**Two measurement errors, same root cause.** The 3.93 us/byte and 1.11 us/byte figures
that drove decisions for a long time are the same artifact measured twice: dividing a
whole `SCAN` call by its payload charges a fixed ~215 us round trip to per-byte
clocking, inflating it about 5x. Marginal-cost differencing gives 0.663-0.715 us/byte
against a 0.667 wire floor.

**And a comparison error of mine, for completeness.** I timed new firmware at 890 ms
against a recorded 1680 ms and reported 1.89x. Different bitstreams. Same payload it was
846 -> 774 ms, or 1.09x. That is what the fixed-payload benchmark exists to prevent, and
it happened before the benchmark existed.

## Repository state worth knowing

The `0xb8` synthetic benchmark and `debris/code/spi-dma-cynthion-d11.c` are
**not on main** — they live in other agents' worktrees, and the two apollo
commits have diverged. The flashed firmware has no `0xb8`, so the synthetic
benchmark was unavailable here; marginal-cost differencing measures the same
thing without needing new firmware.
