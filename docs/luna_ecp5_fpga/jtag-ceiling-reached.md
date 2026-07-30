# JTAG configuration: the path is done, the time is in USB

Two proposals — transmit-only DMA, and raising SCK to 24 MHz — were investigated
and **neither was implemented**, because measurement showed both would be
no-ops or unsafe. This records why, since both looked well-founded.

> **Superseded in part.** DMA *was* implemented later and is worth **-85 ms, 1.26x** --
> the largest code win in this document (`d43f765`, see below). The section immediately
> following is still correct about what it measured, and was still the wrong conclusion:
> it asked whether DMA could remove **per-byte CPU cost** and rightly answered no. The
> value of DMA is not the per-byte term at all. It is that a CPU not spinning on SERCOM
> flags can run `tud_task()`, which is where the ~98 us of NAK per USB transaction was
> going. A correct measurement can still answer the wrong question.

## There is no CPU cost left for DMA to remove (the right answer to the wrong question)

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
accurately recorded, none comparable: different bitstreams, different firmware,
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

| commit / limit | what | 256 B | 512 B | 1024 B |
|---|---|---|---|---|
| **`v1.1.1`** | **stock release** | **713.9 ms** (11.5%) | n/a | n/a |
| `4bf7691` | enable LTO | 639.1 ms (12.8%) | n/a | n/a |
| **`e034daa`** | **pipelined `spi_send` + suppress TDO readback** | **566.9 ms** (14.4%) | n/a | n/a |
| `0e9bfb1` | `JTAG_BUFFER_SIZE` define, no functional change | 562.1 ms (14.6%) | n/a | n/a |
| `bb82d39` | 512-byte buffers + `GET_INFO` | 555.4 ms (14.7%) | 488.9 ms (16.8%) | n/a |
| **`19242e8`** | **two reported limits + 1024-byte writes** | 564.0 ms (14.5%) | 489.5 ms (16.7%) | **457.6 ms (17.9%)** |
| `cd4a85c` | double-buffered staging | -- | 469.1 ms (17.5%) | 455.6 ms (18.0%) |
| **direct USB port** | **no code change -- moved off a 4-hub chain** | -- | **425.2 ms (19.3%)** | **409.1 ms (20.0%)** |
| **`d43f765`** | **clock by DMA, stop blocking `tud_task()`** -- *over flash budget, see below* | -- | **343.3 ms (23.9%)** | **324.4 ms (25.2%)** |
| | | | | |
| **no USB payload** | `0xb9`, pattern generated in firmware | 275 ms (30%) | 137 ms (60%) | not measured |
| **theoretical wire** | 12 MHz SCK, 1 bit per clock | 81.9 ms (100%) | 81.9 ms (100%) | 81.9 ms (100%) |

**Reading the table.** Every time is for the **same 122880-byte payload** -- the columns
are the JTAG *chunk size* the transport used to move it, not different amounts of work.
So a row compares one firmware against itself at different chunk sizes, and a column
compares firmwares at the same chunk size.

The percentage in each cell is **that cell against the theoretical wire time for the
same payload**, 81.9 ms. So `457.6 ms (17.9%)` means: at 1024-byte chunks this firmware
takes 457.6 ms to move 122880 bytes, which is 17.9% of the 81.9 ms the wire alone would
need. Higher is better, 100% is unreachable.

**Cumulative against stock**, each on the best chunk size it supports:
**713.9 -> 457.6 ms, 1.56x.**

**The no-USB row at 1024 B is not measured, and the attempt broke the board twice.**
`0xb9` encoded its chunk size in a single `wIndex` byte with "0 means full buffer",
which was silently wrong once the buffer exceeded 256: asking for 512 truncated to 0,
read as "full buffer", so a 512 x 240 request clocked **245760 bytes in one
uninterruptible loop**. That overran the host's control-transfer timeout and took the
device off the bus entirely -- a physical replug was needed.

Re-encoding it in 64-byte units fixed the truncation, and a 65536-byte work bound was
added. **The bound was off by one case**: 1024 x 64 is exactly 65536, so `> 65536` is
false, the request passes, and the clocking still overruns. It wedged a second time,
though that one recovered without a replug.

So the correct bound is on *time*, not bytes, and it has to account for the whole
control transfer rather than just the clocking. Until that is fixed the 1024-byte
no-USB figure is unavailable -- which leaves the 1024 column without a denominator, the
same gap that the 256-byte column had before it was filled.

**Real configure**, in-process and therefore comparable, same bitstream:
**778.0 ms at 512 B, 741.0 ms at 1024 B.** The shift benchmark and the real configure
agree on direction and roughly on magnitude -- 32.0 ms against 37.0 ms -- which is the
first time in this work the two instruments have been cross-checked.

Every `n/a` is an impossibility rather than a gap: those firmwares declare a 256-byte
buffer and stall anything larger, so the request is refused rather than merely
unnegotiated.

**Cumulative against stock: 713.9 -> 324.4 ms, 2.20x.**

Rows above `cd4a85c` were measured on the four-hub chain and are ~10% pessimistic. They
stay comparable with each other, but **only the `direct USB port` and `d43f765` rows
reflect the current setup**, so those are the ones to quote. Real configure on the same
bitstream, in-process: **693 ms**, against 741 ms on the hub chain.

## DMA is the largest code win, and it does not fit

`d43f765` is worth **-85 ms, 1.26x** -- more than every other firmware change in this
table combined. It is also **over budget: flash 96.65% against a 95% ceiling, RAM 85.16%
against 85%.** The deficit is **237 bytes of flash and 7 bytes of RAM**. It is committed
but *not shippable*, and the ceilings were deliberately not raised.

**Why it worked, when the archived version did not.** `debris/code/spi-dma-cynthion-d11.c`
ran on hardware in an earlier session and measured **+13 to +36 ms slower**, so DMA was
written off. The defect was one line:

    while (!(DMAC->CHINTFLAG.reg & (DMAC_CHINTFLAG_TCMPL | DMAC_CHINTFLAG_TERR)));

It freed the CPU with DMA and then immediately burned it in a spin, so `tud_task()` stayed
blocked for the whole transfer and behaviour was identical to polling plus setup cost.
**DMA was never tested as an asynchronous mechanism.** Arming the channels and returning,
then polling completion from `jtag_scan_task()`, is the entire difference between -36 ms
and +85 ms.

Completion is polled from the task rather than served by a DMA interrupt, and that is
forced: `CHID` is a single register window selecting which channel's registers are
visible, so the channel-setup sequence is not re-entrant and must not run from an ISR.
The poll costs one register read per main-loop iteration against a ~700 us spin, so
nothing is lost.

**Where the 237 bytes went.** About 64 are intrinsic -- DMAC descriptor and write-back
sections the hardware mandates. The rest is splitting one synchronous function into arm
and poll halves whose state must be marshalled through a struct. The cheap savings are
already taken: one aligned allocation instead of two descriptor arrays, a redundant DMAC
reset and two `.bss` memsets dropped, both scan structs packed, an always-true
`discard_tdo` field removed -- about 100 bytes of the original ~520. What remains is a
cheaper arm/poll split, or reclaiming flash from elsewhere in the firmware. Shrinking the
stack reservation does not qualify: the stack is RAM and cannot pay a flash debt.

**The second buffer is not redundant, which was worth checking.** If DMA had made
`jtag_tx_alt` unnecessary, its 512 bytes of RAM would have paid for the change outright.
Forcing the host onto the single-buffer path costs **+74.1 ms (+22.8%)**, so the two
mechanisms are complementary: DMA stops the CPU blocking on the wire, and the second
buffer lets the host's *next* fill land during the current chunk. `scripts/jtag_single_buffer_probe.py`
exists so this is not re-proposed next time flash is tight.

Two rows earn their place by being nearly flat. `0e9bfb1` is a pure refactor and moves
4.8 ms, which sets the noise floor. `cd4a85c` double-buffers the staging so `SCAN` returns
in 139 us instead of ~895 us -- the overlap provably works -- and buys 1.9 ms at 1024 B,
because clocking was only 24% of the total and hiding it was the wrong quarter to chase.

**Every `n/a` is an impossibility, not a gap.** Only HEAD has 512-byte buffers.
Everything before it declares `jtag_out_buffer[256]` and stalls anything larger --
`if (request->wLength > sizeof(jtag_out_buffer)) return false;` -- so a 512-byte
request is refused by the firmware itself. The host would also fall back to 256
regardless, since `GET_INFO` arrived with HEAD; the benchmark detects that and
declines to label a 256-byte run as 512, which is what stops an unimplemented
`GET_INFO` from flattering a result.

Theoretical is chunk-independent by definition: 122880 x 8 / 12 MHz = 81.9 ms,
1500 KB/s. Chunking is a transport concern and the wire does not see it.

**Read the columns, not the diagonal.** Figures in this paragraph are from the hub-chain
rows, so they overstate the USB share slightly, but the shape holds. At 256 B, 280 ms of
555 is USB against 275 ms clocking -- half and half. At 512 B it is 352 of 489 in USB against
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

## USB topology: the largest single win, and not a code change

Apollo was plugged **four chained hubs deep**, on a full-speed segment shared with an
audio device, a HID and two other CDC devices. Moving it to a port one level from the
root hub, with nothing else on that segment:

| | four hubs deep | direct port |
|---|---|---|
| fixed cost per transfer | 144.7 us | **122.7 us** |
| per-byte cost | 2.717 us | **2.267 us** |
| effective throughput | 2.94 Mbit/s | **3.53 Mbit/s** (29% of the bus) |
| shift path, 1024 B chunks | 455.6 ms | **409.1 ms** |
| real configure | 741 ms | **693 ms** |

**46 ms from moving a cable** -- more than double-buffering, the chunk increase, the
pipelined `spi_send()` or the TDO suppression bought individually.

Every full-speed transaction below a high-speed hub is split-transaction scheduled by the
nearest hub, and the 12 Mbit/s of that segment is *shared* with everything else on it.
Four hubs of chaining plus contention was costing 17% of the per-byte rate.

**This should be stated whenever a figure is quoted.** Every measurement in this document
before this section was taken on the hub chain, so they are internally comparable but all
about 10% pessimistic. The table above is the corrected baseline.

### It also bounds the remaining levers more tightly

Since per-byte cost is the wall, and it improved by moving the *host-side* topology rather
than anything on the device, the interesting question is how much of the remaining 2.267
us/byte is still contention rather than protocol. The wire is 0.667 us/byte, so 71% is
still overhead -- but the split between "control transfer protocol" and "this host, this
controller, this cable" is now known to be non-zero and was previously assumed to be zero.

### On Linux-side priority: there is nothing to tune

Checked, because it is the obvious next thought. `usbcore` exposes only `autosuspend`,
`authorized_default` and `initial_descriptor_timeout` -- no scheduling parameters.
Control-transfer priority is fixed by the USB spec (10% of frame budget, best-effort) and
implemented by the host controller, which here is **xHCI** -- so the scheduling happens in
controller firmware walking transfer rings, not in the kernel driver. There is no knob.

### Isochronous would be faster, and is probably not worth it

Full-speed isochronous allows **1023 bytes per endpoint per frame** with *guaranteed*
bandwidth, against control's measured ~360 KB/s:

| | staging for 122880 B |
|---|---|
| control, measured | 340 ms |
| isochronous, theoretical | **120 ms** |

2.8x, and it is the only transfer type with a bandwidth guarantee rather than
best-effort scheduling.

The catch is that **isochronous has no handshake and no retry** -- a dropped packet is
simply gone. For a bitstream that means an integrity layer would have to be built on top:
the ECP5's own CRC detects corruption but cannot recover from it, so a failed configure
would have to be retried whole. That is a large amount of new machinery for a path that is
already at 693 ms.

## The unexplained USB cost, measured: it is bus efficiency, not frame scheduling

This was the largest open question and it now has an answer, which changes what the
remaining levers are worth.

`scripts/usb_transfer_cost.py` issues the same `SET_OUT_BUFFER` at several payload sizes
and fits `cost = fixed + per_byte x size`. Frame scheduling predicts a nearly flat line
-- 8 bytes and 1024 bytes both costing about a 1 ms frame. Per-transfer overhead plus
real payload cost predicts a slope.

    8 B:    215.8 us      wire alone would be    5.3 us
   64 B:    301.4 us                            42.7 us
  256 B:    822.4 us                           170.7 us
  512 B:   1516.7 us                           341.3 us
 1024 B:   2941.3 us                           682.7 us

    fit: 144.7 us fixed + 2.717 us/byte

**It scales, so it is not frame scheduling.** The line is clean and the slope is large:
**2.72 us/byte against a 0.667 us/byte wire**, so 75% of the per-byte cost is overhead.
Effective throughput is **2.94 Mbit/s of a 12 Mbit/s bus -- 25% efficiency.**

That is a property of control transfers on a 64-byte endpoint: each packet carries token,
data and handshake phases, so most of the time is protocol rather than payload. Neither
end controls it.

### What this does to the two remaining levers

The model predicts the measured transport cost closely, which is what makes it usable:

| chunk | chunks | predicted | of which fixed | of which payload |
|---|---|---|---|---|
| 512 B | 240 | 403 ms | 69 ms | 334 ms |
| 1024 B | 120 | 369 ms | 35 ms | 334 ms |

Measured USB portion at 1024 B is 320.6 ms, against 369 predicted -- close enough to
trust the split.

**The payload term is constant at 334 ms and does not depend on chunk size at all.**
That is the wall.

So:

- **Collapsing `SET_OUT_BUFFER` + `SCAN` is worth about 17 ms at 1024 B, not the 51 ms
  estimated earlier.** The 51 ms figure came from 240 chunks at 213.9 us; at 1024-byte
  chunks there are only 120, and the correct fixed cost is 144.7 us. That is **4% of
  total** for a protocol change.
- **Double-buffering's ceiling stands at 137 ms** -- it hides clocking behind USB, and
  USB is still much the longer leg. It remains the largest available lever by a wide
  margin.
- **Larger chunks are now clearly exhausted.** Each doubling halves only the fixed term,
  which at 1024 B is already down to 35 ms of 457.

### And it names the real ceiling

Nothing on this transport beats roughly **334 ms of payload cost plus 137 ms of
clocking**, and those overlap at best. So the floor for this architecture is about
**334 ms**, not the 81.9 ms the wire suggests.

Against that, the current 457.6 ms is **73% of achievable** rather than 18% of
theoretical. The 5.6x gap to the wire is real but mostly not addressable: it is a
64-byte control endpoint on a full-speed bus.

### Bulk would not move the payload term either, and this explains the old result

I first wrote that a bulk endpoint would fix this, "which allows larger packets and far
less per-packet overhead". **That is wrong at full speed, and the descriptors say so.**

Apollo already exposes bulk endpoints -- EP2 OUT and EP3 IN, the CDC console's -- and
both are **`wMaxPacketSize 64`, identical to control.** That is not a design choice: the
USB 2.0 spec caps a full-speed bulk endpoint at 64 bytes. The 512-byte bulk packets worth
having exist only at high speed, which is the FPGA's PHY, not Apollo's.

So bulk offers **no packet-size advantage here**. What it does offer is the removal of
per-transfer framing: a control transfer costs SETUP, DATA and STATUS stages, while bulk
is data packets back to back.

That bounds it precisely. Per 1024-byte chunk the cost is `2 x 144.7 us fixed +
2782 us payload`. If bulk removed the fixed cost **entirely** it would save 289 us per
chunk, or **35 ms of 457** -- about 8%, and only if the per-byte cost were unchanged.

**Which retires the mystery rather than deepening it.** The earlier bulk-streaming attempt
measured 1703 ms against 1683 ms and was recorded as an unexplained failure -- built,
working, and no faster. It is now explained: at full speed, bulk and control move bytes at
the same rate, and the only saving available was the per-transfer framing, which is a
small fraction of the total. The attempt did what it was designed to do; the design could
not have helped much.

### Endpoints available, and one idea parked

What Apollo actually exposes, read off the live device rather than from the source:

| interface | class | endpoints |
|---|---|---|
| 0 | Communications (CDC control) | EP 0x81 IN, interrupt, 8 B |
| 1 | CDC Data | **EP 0x02 OUT and EP 0x83 IN, bulk, 64 B** |
| 2 | DFU runtime | **none** -- EP0 only |

Plus EP0, where **all** JTAG traffic goes today, since the vendor requests are control
transfers. JTAG has no endpoint of its own.

**So a bulk path needs no new endpoint.** The pair already exists, TinyUSB is already
paying for the descriptor space, and CDC is provably idle during a JTAG session -- the
same JTAG-lock argument that justified sharing the buffers, applied to endpoints.
`console_task()` already returns immediately while the lock is held, so those endpoints
are idle rather than merely quiet. An alternate interface setting, or claiming the
interface on a bulk-mode vendor request, would let both use them.

The SAMD11's descriptor table is `sram_registers[8][2]`, so eight endpoints of space.
Reusing CDC's rather than declaring a fourth pair avoids spending any of it.

### Parked: the bootloader could use bulk too

Worth a look, not now.

The bootloader and the application **never run at the same time** -- Saturn-V runs, jumps
to the app, and is gone until the next reset. So the bootloader has the *entire* endpoint
budget free, with nothing to be exclusive with, and it currently does all its flashing
over **EP0 control transfers** at the same 5.8 packets/frame measured above. That is why
flashing a 12 KB image takes as long as it does.

The same 3.3x argument applies, and it is architecturally cleaner than the application
case: no exclusivity reasoning is needed at all, because nothing else exists to contend.

Two things to check before anyone acts on it. The app's DFU interface is **DFU *runtime***
(`TUD_DFU_RT_DESCRIPTOR`, `usb_descriptors.c:104`) and is functional rather than vestigial
-- `vendor.c:441` calls `tud_dfu_runtime_reboot_to_dfu_cb()` -- so it is not dead weight to
be removed. And it has no endpoints, so removing it would free none. The bootloader-side
change is entirely within Saturn-V, which is a separate submodule (`repos/saturn-v`) with
a **2 KB** flash budget and `-flto` already enabled, so there may be no room for a bulk
implementation regardless.

### But a DEDICATED bulk endpoint is a different proposition, and worth 3.3x

The paragraph above concluded that no endpoint type changes the per-byte cost. **That is
wrong**, and the arithmetic that shows it is simple enough that I should have done it
before writing the conclusion.

Packet size is not the variable that matters. **Packets per frame is.**

| | packets per 1 ms frame | us/byte |
|---|---|---|
| full-speed bulk, by spec | **19** (1216 B/frame) | **0.822** |
| measured, control transfers | **5.8** | 2.717 |

So the measured cost is **3.3x worse than a full-speed bulk endpoint can do**, and the
gap is scheduling opportunity, not signalling. A control transfer carries SETUP, DATA and
STATUS stages and the host schedules them conservatively; bulk packets stream back to
back until the frame is full.

Sanity check on the measurement: a 1024-byte control transfer took 2941 us, which is
about three frames, and works out at 174 us per 64-byte packet. A frame holds 1000 us, so
5.8 packets fit. Against 19 for bulk.

What that means for a dedicated bulk path -- **shutting down CDC and using its endpoints
for JTAG, or declaring new ones**:

| | payload for 122880 B |
|---|---|
| today, control | 334 ms |
| bulk at 50% of theoretical | **202 ms** |
| bulk at theoretical | **101 ms** |

Plus the 35 ms of per-chunk framing that disappears when the transfer is one stream
rather than 120 chunks.

**So this is the largest remaining lever by a wide margin** -- bigger than
double-buffering's 137 ms -- and it changes the floor rather than shaving the overhead.
It is also the one that requires the most work: a bulk protocol needs its own framing and
error handling, where a control transfer gets a status stage for free.

### And it re-opens the old streaming result rather than closing it

Two paragraphs ago I wrote that the earlier bulk-streaming attempt was now explained --
that bulk and control move bytes at the same rate at full speed, so it could not have
helped. **That explanation is also wrong.** Bulk should have been up to 3.3x faster on
the payload term, and it measured 1703 ms against 1683 ms.

So the question is back, and sharper than before: **why did a working bulk implementation
perform like control?** Candidates worth testing, none established:

- **The firmware could not keep the endpoint fed.** If it clocks a chunk to the FPGA
  before accepting the next bulk packet, the bus idles and the achieved rate collapses to
  whatever the turnaround allows -- which would look exactly like control.
- **The host submitted synchronously**, one transfer at a time, so the bus idled between
  submissions regardless of endpoint type.
- **`tud_task()` latency**: transfers are processed from the main loop, so completion
  waits on a loop iteration.

All three predict that bulk's advantage is only available with **double-buffering**, since
that is what keeps the endpoint fed while the SERCOM clocks. Which would make the two
levers one change rather than two independent ones.

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

## The RAM work, one change at a time

Four changes, each built, flashed and tested on its own before the next. Two of them
deliberately bought **no** speed, which is the point of testing separately: a speed
change there would have meant something went wrong.

| change | RAM | 512 B time | what it bought |
|---|---|---|---|
| baseline | 84.28% | 489.4 ms | |
| stack sized from measurement | 78.52% | unchanged | **320 bytes** |
| console ring shares the JTAG union | 72.27% | 491.0 ms | **256 bytes** |
| `spi_send()` accepts NULL receive | 72.27% | unchanged | capability |
| `FLAG_DISCARD_TDO` | 72.27% | 492.6 ms | capability |

**576 bytes reclaimed, no measurable speed change.** All three time figures sit inside
a 1.0-6.9 ms spread, so they are one number.

### Why each was tested alone

**Stack from measurement.** `STACK_SIZE=0x2C0` (704 bytes) against 344 measured, so
just over 2x margin. Verified on hardware afterwards -- idle, 300 control transfers, 50
sideband commands, a full JTAG configure -- and the high-water came back **344, exactly
as with 1024 reserved**. That is the expected result and worth stating: usage does not
depend on the reservation, so shrinking it changes nothing until something actually
overflows. On a part with no MPU that would be silent `.bss` corruption rather than a
fault, which is why the margin is generous rather than tight.

**The union.** The console ring and the JTAG buffers share storage, with exclusivity
proven by the JTAG lock spanning `jtag_init()` to `jtag_deinit()` -- and
`console_task()` returning immediately while it is held. Deliberately **not** aliased:
`jtag_in_buffer` and `jtag_out_buffer` stay separate, because `spi_send()` takes both in
one call and overlapping them would corrupt every transfer.

That change introduced one hazard and closed it: the buffers became **pointers rather
than arrays**, so `sizeof()` on them silently yields 4. Six sites used
`sizeof(jtag_out_buffer)` as a bound, including the `SET_OUT_BUFFER` length check --
which would have become a 4-byte limit rejecting every real request. All six now use
`JTAG_BUFFER_SIZE`.

**`spi_send()` NULL receive.** SPI is inherently bidirectional, so the hardware hands
over a byte whether the caller wants it or not, and both store sites wrote to the
caller's pointer unconditionally -- passing NULL wrote to address 0. The `DATA` register
is still read when discarding, and must be: `RXC` stays set until `DATA` is read and the
loop waits on `RXC`, so skipping the read **hangs** rather than merely dropping a byte.

**`FLAG_DISCARD_TDO`.** `ignore_response` previously suppressed only the host's own
`GET_IN_BUFFER` call; the firmware still captured every byte. Bit 2 of the SCAN flags
now tells it. Backward compatible both ways -- older firmware masks only the flags it
knows, so an unrecognised bit is ignored rather than misread, and an older host simply
does not set it.

### What this was all for

`jtag_in_buffer` is now **provably untouched during a configure**, which is the
precondition for handing its 512 bytes to the transmit path. That is the remaining route
to 1024-byte chunks after the direct attempt hit 95% RAM, and is worth roughly 51 ms by
request count.

The two capability changes are the interesting entry in the table precisely because
they moved nothing: they stopped a store into RAM, and the store was never the
bottleneck. Their value is entirely in what they make possible next.

### 1024-byte chunks: the shift path gets faster, and the read bound is the blocker

Attempted properly this time -- not by growing RAM, but by re-carving the union so the
transmit buffer takes the **whole** region and the receive buffer becomes its second
half. That works because `FLAG_DISCARD_TDO` proves the receive half is untouched during
a write.

**RAM did not grow: 70.12%, and after reverting, 59.77%** -- the re-carve stopped
allocating two separate halves, which is a genuine gain the revert kept.

The shift benchmark improved as predicted:

| chunk | chunks | best |
|---|---|---|
| 256 B | 480 | 564.0 ms |
| 512 B | 240 | 490.7 ms |
| **1024 B** | **120** | **457.5 ms** |

33 ms over 512, 106 ms over 256. Real and measured.

**But it was reverted, because of a flaw I introduced.** `GET_INFO` reports one size,
and there are now **two** limits: the transmit buffer is the whole region, while a
capturing scan is bounded by the read half. The host negotiated 1024 and had no way to
know reads must stay under 512. Demonstrated directly:

    capturing scan of  512 B: accepted
    capturing scan of 1024 B: REFUSED (USBError)

`_execute_command()` in `ecp5.py` defaults to `ignore_response=False`, so parts of the
configure sequence do capture TDO. They are small today, but the protocol now has a
limit the host cannot discover.

**The fix is to report both limits** -- `GET_INFO` already returns eight bytes with four
unused, so a read limit fits with no new request. That is the next change rather than a
blocker.

### A measurement error worth recording, because it nearly reversed the conclusion

The real configure appeared to get **slower** at 1024 -- 831 ms against a recorded
774 ms at 512 -- while the shift benchmark got faster. Six samples said 832-854 ms, so
it was not noise, and the contradiction looked like a real regression.

It was the measurement. Those figures timed `subprocess.run(python cli.py configure)`,
which includes **Python interpreter startup** -- roughly 80 ms that has nothing to do
with JTAG. Measured in-process instead, same session, both sizes back to back:

    configure at 256 B: 850.9 ms
    configure at 512 B: 776.1 ms

The 774 ms reproduces exactly. So the "1024 is slower" reading was an artifact of
process startup, and the earlier 774 figure was only comparable because it happened to
be measured the same way.

**The lesson is the same one the fixed-payload benchmark exists for**, arriving from a
new direction: it is not enough to fix the payload, the harness has to be fixed too. A
per-process measurement cannot resolve a 33 ms difference when startup costs 80.

## Things tried that did not work

Recorded so they are not retried, and because the failures were more informative than
some of the successes.

| attempt | outcome | why |
|---|---|---|
| `JTAG_BUFFER_SIZE` 512 -> 1024 | **rejected at 95.12% RAM** | doubling costs BOTH halves of the pair, 1024 bytes not 512 |
| SCK 12 -> 24 MHz | **rejected, unsafe** | divider steps 8/12/24 with nothing between; SAMD11 `tSCK` min 84 ns = 11.9 MHz rated, so 12 is already past |
| SERCOM DMA, *spinning on completion* | **implemented, marginally slower -- and the conclusion was wrong** | 1711-1751 ms against 1698-1715 polled. It spun on `TCMPL` after arming, so `tud_task()` stayed blocked and it was polling plus setup cost. Made asynchronous instead: **-85 ms, 1.26x** |
| DMA making the second buffer redundant | **hypothesis, disproven** | removing `jtag_tx_alt` costs +74.1 ms (+22.8%); DMA and double-buffering are complementary, not alternatives |
| TX-only (drop TDO entirely) | correct but not worth it | ~2 ms of 950, for a silent-failure surface |
| bulk streaming | **built, worked, no faster** | 1703 vs 1683 ms; its stated cause was later disproven, so still unexplained |
| `-fstack-usage` for stack depth | **wrong tool** | LTO inlines across units, so per-function frames stop matching the final binary |
| `git bisect` across apollo history | **failed on all 5 points** | checking out old apollo replaces `apollo_fpga/`, removing `boot_to_dfu()` -- one of our own additions |
| paint-and-measure, first version | **self-contradictory** | LTO resolved `&_sstack` differently per inlined copy; reported full-region use AND no overflow |
| word-wise stack scan | **latent bug** | one coincidental `0xDEADBEEF` truncates the scan and understates usage |

Five of these are worth more than a note.

**The DMA row is the cautionary one in this whole table.** It reads as "tried, does not
work" and it stopped anyone retrying for months, when the mechanism was right and one
line was wrong. The archived file even documents its own spin in a comment. The lesson is
narrower than "retry failed things": a negative result is only as broad as what was
actually varied, and what was varied there was *who clocks the bytes* -- never *whether
the CPU is free while they are clocked*. Record what a failure tested, not just that it
failed.

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

`debris/code/spi-dma-cynthion-d11.c` was recovered from a lost worktree and is now on
main, which is the only reason the DMA work could be resumed rather than rewritten.

`repos/apollo` sits on a **detached HEAD** at `d43f765` -- normal for a submodule, but the
parent repo's pointer has not been moved, so the DMA commit is reachable only from the
submodule's own reflog until that is decided. It is over budget, so moving the pointer
would put an unshippable firmware on main; that is the reason to leave it, not an
oversight.
