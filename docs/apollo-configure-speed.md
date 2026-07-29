# Speeding up `apollo configure`

Making ECP5 SRAM configuration over JTAG faster. All numbers below are **measured on
hardware** (Cynthion r1.4, SAMD11D14 Apollo, 304726-byte bitstream) unless explicitly
marked as an estimate. Correctness was gated on the ECP5 status register, not on the
absence of an exception — see "Verifying correctness".

## Result

| configuration | time (best of N) | throughput | speedup |
|---|---|---|---|
| baseline | 2575 ms | 115.6 KiB/s | 1.00x |
| \+ host: suppress TDO readback | 1864 ms | 159.6 KiB/s | 1.38x |
| \+ firmware: pipelined SPI | **1680 ms** | **176.7 KiB/s** | **1.53x** |

Verified over 6 consecutive runs at 1674-1687 ms, `DONE=1`, no error bits.

Cost of the firmware change: **+28 bytes of flash** (11204 → 11232 B, 78.15% → 78.35%
of the 14 KB application region), **no additional RAM**.

## Where the time actually goes

Baseline decomposition, per USB request stage:

| stage | total | requests | per request |
|---|---|---|---|
| `SET_OUT_BUFFER` | 994 ms | 1212 | 820 µs |
| `GET_IN_BUFFER` | 739 ms | 1221 | 606 µs |
| `SCAN` | 577 ms | 1221 | 465 µs |
| `GO_TO_STATE` | 198 ms | 67 | 2960 µs |

The brief predicted `SET_OUT_BUFFER` and `SCAN` would dominate. They do, but the
decomposition surfaced a third item the brief did not mention: **`GET_IN_BUFFER` was
28% of total runtime, reading back TDO data that `configure()` discards.**

## Change 1 — stop reading back data nobody uses (host only)

The bitstream burst is write-only. The ECP5 returns nothing meaningful on TDO while a
bitstream is being shifted in, and `configure()` throws the response away. But the
chunked scan path read it back anyway, one `GET_IN_BUFFER` control transfer per chunk.

`shift_data()` already had an `ignore_response` parameter documented as exactly this
optimisation ("allows for a slight performance optimization, as we don't have to
shuttle data back"). It was used for background-SPI flash operations but never for the
bitstream burst. Plumbing it through `_execute_command()` and setting it on the burst
dropped `GET_IN_BUFFER` from 1221 requests to 30.

**2575 ms → 1864 ms.** Three lines, host-side only, no firmware change, no risk.

## Change 2 — pipeline the SPI transfer (firmware)

`spi_send()` on the SAMD11 was fully serialised per byte:

```c
while (DRE == 0);      // wait for room
DATA = byte;           // send
while (RXC == 0);      // wait for the whole byte to come back
received = DATA;       // then start the next byte
```

Waiting for `RXC` before queueing the next byte leaves SCK idle for roughly a full byte
period between bytes. The SERCOM has separate TX-empty (`DRE`) and RX-complete (`RXC`)
flags precisely so the next byte can be queued while the current one is still on the
wire.

The rewrite primes the first byte, then for each subsequent byte waits for `DRE` and
queues it **before** draining the previous byte's `RXC`:

```c
while (DRE == 0); DATA = to_send[0];          // prime
for (i = 1; i < length; ++i) {
    while (DRE == 0); DATA = to_send[i];      // queue next
    while (RXC == 0); received[i-1] = DATA;   // then drain previous
}
while (RXC == 0); received[length-1] = DATA;  // final byte
```

**`SCAN` dropped from 465 µs to 311 µs per request; 1864 ms → 1680 ms.**

### A bug worth recording

The first version of this used a single loop polling `DRE` and `RXC` together with a
`tx - rx < 2` window. It was faster but **silently corrupted TDO** — the ECP5 then
reported an all-zero status register and configuration failed with "Failed to enter
ISC". Reading `DATA` only when `RXC` happened to be set, while racing `DRE` writes,
drops received bytes.

The strict ordering above is what makes the pipelined version correct: every queued
byte is matched by exactly one blocking `RXC` read, so no received byte is lost. This
is the reason the correctness gate below exists — the broken version still "succeeded"
from the host's point of view on the write path.

## What did NOT work

### Bulk-endpoint streaming with a destructive fast mode — no measurable benefit

Implemented in full and tested on hardware: a vendor request (`0xb7`) that puts Apollo
into a one-shot streaming mode, takes over the CDC console for staging, clocks the
bitstream out as it arrives on bulk endpoint `0x02`, then reboots via
`NVIC_SystemReset()` (which sets `PM_RCAUSE_SYST` and so returns to the application
rather than the bootloader). Cost: +256 B flash, +512 B RAM.

It worked — the stream completed and the device rebooted and re-enumerated as designed.
But it was **not faster**:

| path | 304726 bytes |
|---|---|
| chunked control transfers | 1683 ms |
| bulk streaming | 1703 ms |

**Measured, not estimated.** The change was reverted.

Two host-side gotchas found along the way, worth knowing if this is revisited:

- The bulk endpoint belongs to the CDC data interface, which Linux binds to `cdc_acm`.
  libusb writes fail with `Resource busy` until the kernel driver is detached.
- Entering the mode escalates Apollo's control-plane state to `MODE_JTAG_PROGRAMMING`.
  If the stream never completes, that state latches and `force_fpga_offline` is refused
  (surfacing as a pipe error). `VENDOR_REQUEST_EMERGENCY_RESET` (`0xec`) clears it.

### Why streaming didn't help

Timing `SCAN` in isolation — the request that only clocks an already-staged 256-byte
buffer out over JTAG, with no payload transfer at all:

```
SCAN of 256 bytes: 1006 µs/call -> 3.93 µs/byte
implied JTAG-limited floor for 304726 B: ~1198 ms
```

At BAUD=1 on a 48 MHz core, SCK is 12 MHz, so 256 bytes should take ~171 µs on the
wire. It takes 1006 µs. **The MCU-side clocking loop, not USB and not the JTAG wire, is
now the bottleneck**, which is why removing USB round trips bought nothing.

## Latency-bound or bandwidth-bound?

This was measured directly (`scripts/measure_usb_latency.py`), because it determines
whether the fix is "fewer round trips" or "higher throughput per transfer". The answer
is **both, and the mix matters**:

```
round-trip floor (GET_STATE, 0 payload bytes):   223.4 us/req

  size     us/req    us/byte   marginal us/B
     1      214.7    214.693              --
    64      288.1      4.502           1.166
   128      470.4      3.675           2.013
   256      840.5      3.283           2.454

256B call is 3.91x the cost of a 1B call
fixed per-request cost   ~=   214.7 us
marginal cost per byte   ~=   2.454 us/B (628.3 us over a 256B chunk)
```

A 1-byte request costs the same as a zero-payload one (~215 µs), so there is a real
fixed round-trip cost. But at the 256-byte chunk size actually used, **~628 µs of the
~840 µs is size-dependent** — 75% bandwidth, 25% latency.

Note that 2.45 µs/byte is ~3.3x what a 12 Mbps full-speed bus needs for a byte
(0.75 µs). This is control-transfer *protocol* overhead — 64-byte packets with
per-packet handshaking on EP0 — not the wire rate. That is also why moving to bulk
transfers, which have the same 64-byte packet size at full speed, changed so little.

## Verifying correctness

Speed changes here can corrupt data while still appearing to succeed, so
`scripts/verify_configure.py` reads the ECP5 status register after each configure and
asserts `DONE` is set with no `FAIL` or BSE error bits. The racy SPI version described
above passed a naive "did it throw?" check and failed this one.

One operational note: a configured FPGA drives the shared lines and will not re-enter
ISC, so **every configure must start from an offline FPGA**. Back-to-back configures
without `force_fpga_offline()` fail with "Failed to enter ISC" and an all-zero status.
This is pre-existing behaviour, not caused by these changes, but it produces confusing
failures when benchmarking in a loop.

## Where the remaining time goes

Of the current 1680 ms:

- `SET_OUT_BUFFER` — 1000 ms (1212 requests, 825 µs each). Still the largest item, and
  ~75% of it is control-transfer bandwidth overhead.
- `SCAN` — 380 ms. Now bounded by the MCU's byte-at-a-time SERCOM loop (3.93 µs/byte
  against a 0.67 µs/byte wire), not by USB.
- `GO_TO_STATE` — 196 ms across only 67 requests, at ~2930 µs each. Anomalously
  expensive per call and not yet investigated.

The most promising next step is **DMA for the SERCOM transfer**, which would attack the
3.93 µs/byte clocking cost directly; the SAMD11 has a DMAC that can service SERCOM.
That would make the USB side the bottleneck again, at which point bulk streaming — which
bought nothing today — would become worth revisiting. This ordering matters: streaming
was implemented before the clocking cost was understood, and that is why it failed.

## Reproducing

```bash
# End-to-end timing, with per-stage decomposition.
python3.15t scripts/measure_configure.py --label baseline --repeat 3 --decompose

# Correctness gate: asserts DONE with no error bits.
python3.15t scripts/verify_configure.py --repeat 6

# Latency vs bandwidth discriminator.
python3.15t scripts/measure_usb_latency.py
```

All three log to `./tmp/logs/<name>.log` as well as the terminal.

Firmware build and flash:

```bash
cd repos/apollo/firmware
make APOLLO_BOARD=cynthion BOARD_REVISION_MAJOR=1 BOARD_REVISION_MINOR=4
make APOLLO_BOARD=cynthion BOARD_REVISION_MAJOR=1 BOARD_REVISION_MINOR=4 dfu
```

A known-good image is kept at `tmp/firmware-backup/firmware-known-good.bin`; restore it
with `fwup-util --device 1d50:615c <image>`. This was used successfully during this work
after the streaming experiments destabilised the device, so the path is known to work.
