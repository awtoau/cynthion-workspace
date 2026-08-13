# The binary record stream — the wire format

**One framed, versioned, timestamped record format for the board to push data at
a host.** The text shell is one front end over the drivers; this is the other
([#303](https://github.com/awtoau/cynthion-workspace/issues/303),
[#250](https://github.com/awtoau/cynthion-workspace/issues/250)).

Host side: [`../scripts/soc_stream.py`](../scripts/soc_stream.py) — decoder,
reference encoder, and the golden frames the firmware is checked against.
Frozen byte-for-byte in [`../tests/test_soc_stream.py`](../tests/test_soc_stream.py).

**Nothing on the board emits records yet.** No firmware producer, no shell
command to enter binary mode. This file is the contract both sides are written
against; the order of work is at the end.

## What it is, and is not

* **Is**: a device-to-host push stream of fixed-layout records, board-stamped,
  sequence-numbered, framed so a host can join mid-stream.
* **Is not**: a command protocol. The host-to-board surface is two bytes
  (below). Everything else a host might want to *set* is a driver operation and
  waits for [#303](https://github.com/awtoau/cynthion-workspace/issues/303).
* **The first slice is read-only on purpose.** A reader duplicates no driver
  semantics, so it needs none of [#303](https://github.com/awtoau/cynthion-workspace/issues/303)'s refactor to be correct. Writes do —
  a binary front end that reimplemented `power::mv_to_limit_code` would
  reintroduce the 2× scale bug of [#270](https://github.com/awtoau/cynthion-workspace/issues/270) independently.

## Transport: console 0, in band, mode-switched

| candidate | verdict |
|---|---|
| **console 0** — the FPGA's own USB CDC (`gateware/soc/top.py:1537-1559`, PID `riscv_console`) | **chosen**: exists, enumerated, `scripts/tio_user.py --serve` already fans it out 8-bit clean (`tio_user.py:298`) |
| console 1 — the Apollo-facing UART | works unchanged (see Apollo, below), but 115200 and drops out under JTAG. A fallback, not the path |
| a second CDC | LUNA's `USBSerialDevice` is single-function; a new descriptor set and endpoint pair. Worth doing *after* the format has a consumer |
| USB bulk from the SoC | there is no USB device controller — `firmware/cynthion-soc/src/usb.rs:14-18` |
| FPGA_ADV sideband | Apollo is master and the FPGA cannot initiate ([#176](https://github.com/awtoau/cynthion-workspace/issues/176)). Fixed-length CRC-8 commands, no push path |
| JTAG | ER1's `JTAGStager` holds the CPU in reset (`gateware/soc/top.py:1456`); it cannot observe a running board |

Two rules protect the one diagnostic path a person has:

* **Records only in binary mode**, entered by a text command and left by one
  byte. Precedent: `load <hex>` (`firmware/cynthion-soc/src/staging.rs:26-91`).
* **No interleaving with text.** `tio_user.py:293` writes the same bytes to the
  operator's terminal with `decode("ascii", "replace")`, so a record on the wire
  is replacement characters on their screen. Async log lines that must escape
  during a stream go out as `TEXT` records instead.

Two defects in `load` not to repeat, both at `staging.rs:42-72`: it spins for
`len` bytes with **no timeout and no abort**, and it holds RTIC's `devices` lock
throughout (`rtic_app.rs:319-321`). The stream loop polls `irq::pop`
non-blockingly between records, so a host that stops reading does not wedge it.

## The frame

```
C0 | kind:u8  ver|flags:u8  len:u16  seq:u32  at:u32 | body[len] | crc32:u32 | C0
     \________________ 12-byte header _____________/              \_ over header+body
```

* **Little-endian throughout** — the CPU's order, and `struct.unpack("<…")` on
  the host.
* **SLIP framing**, RFC 1055: `END = 0xC0`, `ESC = 0xDB`, `ESC END = 0xDC`,
  `ESC ESC = 0xDD`. Escaping applies to header, body and CRC alike.
* Minimum frame is 16 bytes unescaped (12 + 4); an empty body is legal.
* `MAX_BODY = 256`. Fixes the firmware's staging array — there is no allocator.
  A host refuses a longer `len` rather than growing to meet it.

### Why SLIP

* **Not COBS** — its first byte is a forward pointer, so a whole record must be
  buffered before any of it is sent. SLIP streams a byte at a time.
* **Not magic-word-plus-length** — a corrupt length walks the parser into the
  stream and it resyncs by luck. A reserved delimiter cannot occur in a body.
* **Not hex or base64 over the text channel** — considered, because it needs no
  mode switch and stays human-visible. Rejected: 1.33–2× the bytes, and the
  point is to stop the board formatting numbers a host will parse back.
* `0xC0` is not ASCII, so a delimiter is never shell output.
* Cost: 2 delimiter bytes per record, plus one per `0xC0`/`0xDB` in the payload.

### Why CRC-32, and why there is one at all

* **Already in the image.** `firmware/cynthion-soc/src/hyperram.rs:100-118` is a
  bitwise CRC-32 — init `0xffffffff`, reflected `0xedb88320`, final NOT. That is
  CRC-32/ISO-HDLC, so the host side is `zlib.crc32` and neither end hand-rolls a
  loop. Reusing it costs no new `.text`; a CRC-16 would be a new implementation
  on both sides to save two bytes a record.
* **Two independent places drop bytes silently**, and either leaves a truncated
  frame that still looks plausible:
  * `Uart::put` abandons a byte after 200,000 spins (`uart.rs:329-339`) — no TX
    ring, no transmit interrupt, no backpressure.
  * Apollo's console bridge drops the **oldest** byte when its 256-byte ring
    overflows (`repos/apollo/firmware/src/console.c:48-59`).

### The header

| off | field | notes |
|---|---|---|
| 0 | `kind:u8` | see the table below |
| 1 | `ver:u4 \| flags:u4` | version in the **high** nibble, on **every** record |
| 2 | `len:u16` | body bytes, `0..=256` |
| 4 | `seq:u32` | monotonic across the session, wraps; a hole is loss |
| 8 | `at:u32` | milliseconds, `timer::millis()` (`timer.rs:300`), captured at production |

Flags: `0x1 RESYNC` (first record of a session), `0x2 TRUNC` (producer clipped
the body). `0x4`/`0x8` reserved, must be zero.

`at` is the same millisecond clock the event ring already stamps with
(`events.rs:328`) — u32, wraps at 49.7 days, zero at boot. **Sub-millisecond
resolution is a body's own business**: `clock::now()` is 32-bit ticks that wrap
every 71.6 s at 60 MHz (`clock.rs:39-81`), so a producer that needs it carries
its own tick field and the host scales by `time_hz` from `SESSION`. That is how
[#278](https://github.com/awtoau/cynthion-workspace/issues/278)'s
`Sample { ticks, vbus, vsense }` fits: it is a body, batched N per record, under
this envelope — not a second envelope.

## Kinds

`0x00`–`0x0f` are the stream's own affairs and their layouts are frozen here,
because a host needs them before any catalogue has arrived. `0x10`+ are
producers, and their layouts arrive at run time.

| kind | name | body |
|---|---|---|
| `0x00` | SESSION | `<BBHIIII` — `format_version, kinds, reserved, firmware_git, gateware_git, time_hz, session_id` |
| `0x01` | DROP | `<III` — `lost, from_seq, at_first_ms` |
| `0x02` | CATALOGUE | one per producer kind; layout below |
| `0x03` | TEXT | raw console bytes — an async log line, so it does not corrupt the stream |
| `0x04` | ERROR | `<II` — `code, value`; the stream's own faults |
| `0x10` | POWER | `<IiIiIiIiI` — `bus_mv, current_ua` per channel, then the latching tick |
| `0x20` | EVENT | `<IQI` — the event ring slot verbatim: `code, value, at_ms` |
| `0x30` | BIST | allocated, **layout deliberately unspecified** — no producer exists, and the catalogue means one need not be frozen first |

Notes that are the difference between a right and a wrong reading:

* **POWER's current is signed.** `Reading { bus_mv: u32, current_ua: i32 }`
  (`power.rs:410-413`) — the switch tree is bidirectional, and an unsigned
  decoder reads −12 mA as 4.29 A.
* **POWER's channel order is `power.rs:261`** — `target_a, target_c, aux,
  control` — which is not connector order.
* **POWER's last field is `latched_ticks`**, the `clock::Instant` of the REFRESH
  that latched the sample, not of the read that fetched it (`power.rs:417-425`).
  It is how a host tells a fresh sample from the same cached one sent twice.
* **EVENT is undecoded.** `code` is packed `0xTT_SS_NNNN` — tag, subsystem,
  number (`events.rs:57-62`) — and `at_ms` is when the ring was *pushed*, which
  is not the `at` in the envelope. The difference is the point.

### CATALOGUE, and why the records are fixed rather than TLV

Body: `kind:u8 name_len:u8 fmt_len:u8 fields_len:u8` then the three strings,
unterminated, in that order. `fmt` is a Python `struct` format; `fields` is
comma-separated names, one per format field.

* **TLV pays a tag and a length on every field forever**, to buy a flexibility
  exercised once per firmware change. A 36-byte POWER body would be ~60.
* **The catalogue buys the same property once**: the board describes its layouts
  at session start, the host builds parsers from them, then parses fixed structs
  at rate. Self-describing at handshake, fixed on the wire.
* **The board is authoritative.** `soc_stream.KNOWN` is a transcription of the
  driver types and exists only to *disagree loudly*; on a mismatch the board's
  layout is used and a warning is recorded. Silently preferring either copy is
  the two-declarations-of-one-truth defect.
* Cost on the board: one `&'static str` trio per kind in `.rodata`, ~90 bytes
  for POWER.

## Versioning: refuse, do not guess

* **The version is in every header**, not only in `SESSION`. A host that joined
  mid-stream would otherwise parse v2 records with v1 rules — and the CRC would
  pass, because the producer computed it.
* An unrecognised version → the frame is **dropped and counted**. No record is
  ever emitted from bytes whose layout is not understood.
* The version is checked **after** the CRC, so a corrupted byte reports as
  corruption rather than as a firmware from the future.
* The version covers the **envelope only**. Producer layout changes need no bump
  — that is what the catalogue is for.
* `firmware_git` and `gateware_git` are **build** identity, not format identity:
  they warn, they do not gate. Same `GIT_WORD` encoding as `info.rs` (bit 31 =
  dirty, `build.rs:112`).

## Joining mid-stream

1. Discard bytes until the first `END`. A host that finds none is looking at the
   text shell, and should say so rather than wait.
2. Frames between `END`s; empty frames ignored.
3. `RESYNC` marks the first record of a session, so "I joined late" and "the
   board just started" are distinguishable.
4. `session_id` changing means a new session — `seq` restarts, and that is not
   a gap. It is `clock::now()` at binary-mode entry: not unique, only different.

A truncated frame — the Apollo ring dropping its oldest bytes mid-record —
fails its length check or its CRC, is counted, and the **next** complete frame
decodes. That is the property length-prefixed framing does not have.

## Loss is announced twice, deliberately

* `seq` is monotonic, so a host sees a hole unaided. That is **the wire losing
  what the board sent**.
* `DROP` says how many and from when. That is **the board overrunning its own
  producer**. The counters exist already: `events.rs:349` `DROPPED`, `:352`
  `REPORTED`, `:358` `LOST_FROM`, and `drain` already reports once per burst
  rather than once per record (`:487-503`).
* Either can be the only one that fires. Two signals for one loss is the point,
  not redundancy.

## Host to board

Two raw bytes, unframed, in binary mode only. The board's receive path is a
byte-at-a-time state machine and this is the whole of it.

| byte | meaning |
|---|---|
| `0x03` | ETX — leave binary mode, back to the text shell |
| `?` | re-emit `SESSION` and the catalogue |

Anything richer is a driver operation
([#303](https://github.com/awtoau/cynthion-workspace/issues/303)), not a
protocol feature.

## Is this the Apollo link? No — and the question splits in two

**Apollo's own protocol: a category error.** It is USB **vendor control
transfers**, host-initiated, with no push path and no framing:

* `repos/apollo/apollo_fpga/__init__.py:361-374` — the only two primitives, both
  `TYPE_VENDOR | RECIP_DEVICE` `ctrl_transfer`. Request IDs `0xa0`–`0xed` at
  `:65-73`; firmware enum and dispatch at
  `repos/apollo/firmware/src/vendor.c:33-103` and `:585-661`.
* `vendor.c:4-6` refuses libgreat outright — *"we want to avoid the overhead of
  the libgreat comms API"*.
* No bulk path for debug traffic: `CFG_TUD_VENDOR 0`
  (`boards/cynthion_d11/tusb_config.h:79`), **one** CDC (`:69`), and the only
  bulk endpoints on the device belong to that CDC
  (`mcu/samd11/usb_descriptors.c:101`).
* Payloads are fixed-size structs packed by hand. No SLIP, no COBS, no length
  prefix, no CRC on that path.
* GCP/libgreat is in the tree only as vendored upstream for Moondancer
  (`repos/cynthion/firmware/libgreat/src/gcp.rs`), is request/response with no
  push path, and rides a USB control endpoint this SoC does not have.

**Apollo's CDC bridge: not a protocol at all, a wire.** It carries bytes and
never inspects them, so this format rides it unchanged with no Apollo firmware
change:

* `repos/apollo/firmware/src/console.c:116-132` — UART ring bytes into
  `tud_cdc_write_char()`, CDC bytes out to `uart_nonblocking_write()`. Byte for
  byte, no interpretation.
* SERCOM2 on PA14/PA11 (`boards/cynthion_d11/uart.c:27,39-40,114-115`), which is
  the SoC's console 1 (`gateware/soc/top.py:1612-1681`, `APOLLO_UART_BAUD =
  115200` at `:291`).

Three properties make it a fallback rather than the path:

* **115200 8N1 → 11.5 kB/s**, against a USB CDC on console 0.
* **The FPGA must never speak first.** T14 is JTAG TMS
  (`gateware/soc/top.py:181-199`, `firmware/cynthion-soc/src/target.rs:210-216`),
  so binary mode there is opt-in per console.
* **An Apollo JTAG session stops it dead.** `console.c:106-108` returns early
  while JTAG is active and `jtag.c:19` takes the pinmux, so the stream truncates
  mid-frame with nothing said. SLIP plus `seq` is what turns that into a counted
  gap instead of a silent one.

The sideband is a third thing again, and also not this: fixed-length commands
with a trailing CRC-8, Apollo-mastered, *"lengths are fixed and there is no
framing"* (`repos/apollo/firmware/src/boards/cynthion_d11/fpga_adv.c:463-465`),
reached from the host through vendor request `0xc3` (`vendor.c:388-435`).

> Apollo citations are from the checkout at the SHA this repo pins,
> `90c8b7b`. Uncommitted edits in that checkout were not checked.

## Cost

* **On the board today: zero.** Nothing here is implemented in firmware.
* The CRC is free — already linked (`hyperram.rs:100`, used by `staging::load`).
* The only measurement that exists is [#250](https://github.com/awtoau/cynthion-workspace/issues/250)'s prototype: **1,382 B `.text`, 372 B
  `.rodata`**, `riscv32imac-unknown-none-elf`, `opt-level = "z"`, `lto = true`,
  `codegen-units = 1`. It used different framing and a different CRC from this
  spec, so treat it as an order of magnitude, not a number for this design.
* Alongside the text shell the cost is **additive**: `core::fmt`'s machinery is
  already in the image and shared. The size win is being able to *not build* the
  shell, which needs the boundary first — [#303](https://github.com/awtoau/cynthion-workspace/issues/303)'s own conclusion.

## Known interactions

* **`fmt::Write for Uart` inserts `\r` before every `\n`** (`uart.rs:452-458`).
  A record emitter must call `Uart::put` directly; going through `write!` would
  corrupt any body containing `0x0A`.
* **`soc_test.py` asserts no bare LF reaches the wire** (`uart.rs:450`). Binary
  bodies contain `0x0A` without a preceding `0x0D` by construction, so that
  check has to be scoped to text mode before a producer lands.
* **The GUI daemon transcodes, it does not forward.** The Flutter client is
  newline-delimited JSON over a WebSocket and drops binary frames
  (`gui/lib/services/transport/wifi_transport.dart`), so
  [#249](https://github.com/awtoau/cynthion-workspace/issues/249)'s daemon owns
  the port, decodes records and emits NDJSON.

## Order of work

1. Framing, `SESSION`/`CATALOGUE`/`DROP`, and the mode switch. **Done here on
   the host side**; the firmware half is next. Resync and versioning are the
   parts that are hard to change later.
2. `POWER` — the panel [#249](https://github.com/awtoau/cynthion-workspace/issues/249) shows as synthetic, and the driver already returns
   the record (`power::latest()`).
3. Measure console 0's real device-to-host byte rate. `gateware/soc/top.py:1600`
   sets `serial.tx.last.eq(1)`, one USB IN transaction per byte — correct for a
   console, and the throughput ceiling for anything else. Only then decide
   whether a packing bit, a second CDC or bulk is needed.
4. `EVENT`, then `BIST`.
