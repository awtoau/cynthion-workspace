# Draft comment for #250 — NOT POSTED

Public repo, so this needs two explicit approvals from Dan before it goes up.
Scrubbed: no absolute filesystem paths, no credentials, nothing about the RE work.

---

## The format is written down and the host side is landed. The Apollo answer is: two different links, and one of them is not a protocol at all

* `docs/binary-protocol.md` — the wire format.
* `scripts/soc_stream.py` — decoder, reference encoder, golden frames. `./dev.py stream --self-test`.
* `tests/test_soc_stream.py` — 30 checks, no board.

**Nothing on the board emits records yet.** No firmware producer, no command to
enter binary mode. This is the contract both sides get written against.

### The envelope

```
C0 | kind:u8  ver|flags:u8  len:u16  seq:u32  at:u32 | body[len] | crc32:u32 | C0
     \________________ 12-byte header _____________/              \_ over header+body
```

Little-endian. SLIP (RFC 1055). `MAX_BODY = 256`, which fixes the firmware's
staging array — there is no allocator.

Four choices differ from the earlier sketch in this issue, each for a reason:

* **The version is in every header, not only in `SESSION`.** High nibble of byte
  1. A host that joins mid-stream would otherwise parse v2 records with v1 rules
  and *the CRC would pass*, because the producer computed it. That is exactly the
  misparse the issue asks to make impossible. It costs no bytes.
* **CRC-32, not CRC-16.** `firmware/cynthion-soc/src/hyperram.rs:100-118` already
  has a bitwise CRC-32 in the image — init `0xffffffff`, reflected `0xedb88320`,
  final NOT, which is CRC-32/ISO-HDLC. So the board reuses a function already
  linked (`staging::load` calls it) and the host calls `zlib.crc32`. A CRC-16
  would be a new implementation on each side, each able to be wrong differently,
  to save two bytes a record. The test transcribes the Rust loop and compares it
  to `zlib`, and guards the polynomial and the final NOT against being changed.
* **`CATALOGUE` is the versioning story for bodies.** The envelope version covers
  the envelope only; producer layouts arrive at run time as `struct` format
  strings plus field names. A firmware that adds a field stays decodable by a
  host that predates it. `soc_stream.KNOWN` is a *cross-check* that warns and
  defers to the board — never the source.
* **`BIST` is allocated and deliberately unspecified.** No producer exists, and
  the catalogue means a layout need not be frozen before one does.

### Reconciling #278

#278's `Sample { ticks: u32, vbus: [u16;4], vsense: [u16;4] }` is a **body**
under this envelope, batched N per record — not a second envelope. The split that
makes that work:

* Envelope `at` is **milliseconds**, `timer::millis()` (`timer.rs:300`), the same
  clock the event ring stamps with (`events.rs:328`). u32, wraps at 49.7 days.
* **Sub-millisecond resolution is a body's own business.** `clock::now()` is 32
  bits of ticks that wrap every 71.6 s at 60 MHz (`clock.rs:39-81`), so a
  producer that needs it carries its own tick field and the host scales by
  `time_hz` from `SESSION`.

`POWER` already does this: its last field is the `clock::Instant` of the REFRESH
that latched the sample, not of the read that fetched it (`power.rs:417-425`).
It is how a host tells a fresh sample from the same cached one sent twice.

Two things a wrong `POWER` decoder gets wrong, both now pinned by a test:
`current_ua` is **i32** (`power.rs:410-413`) — the switch tree is bidirectional,
and unsigned reads −12 mA as 4.29 A — and channel order is `power.rs:261`
(`target_a, target_c, aux, control`), which is not connector order.

### Why the CRC and the sequence number are not belt-and-braces

**Two independent places drop bytes silently**, and either leaves a truncated
frame that still looks plausible:

* `Uart::put` abandons a byte after 200,000 spins (`uart.rs:329-339`). No TX
  ring, no transmit interrupt, no backpressure.
* Apollo's console bridge drops the **oldest** byte when its 256-byte ring
  overflows (`repos/apollo/firmware/src/console.c:48-59`).

That second one also settles the framing argument: bytes vanish from the
*middle*, so a length-prefixed frame resyncs by luck while SLIP resyncs at the
next delimiter. There is a test for exactly that case.

---

## Is this used for, or shared with, the Apollo link?

The question splits, and the two halves have different answers.

### Apollo's own protocol — a category error

* **USB vendor control transfers, host-initiated, no push path.**
  `repos/apollo/apollo_fpga/__init__.py:361-374` is the whole transport: two
  primitives, both `TYPE_VENDOR | RECIP_DEVICE` `ctrl_transfer`. Request IDs
  `0xa0`–`0xed` at `:65-73`; firmware enum and dispatch at
  `repos/apollo/firmware/src/vendor.c:33-103` and `:585-661`.
* **No bulk path for debug traffic.** `CFG_TUD_VENDOR 0`
  (`.../boards/cynthion_d11/tusb_config.h:79`), **one** CDC (`:69`), and the only
  bulk endpoints on the device belong to that CDC
  (`.../mcu/samd11/usb_descriptors.c:101`).
* **No framing of any kind on that path.** Payloads are fixed-size structs packed
  by hand. No SLIP, no COBS, no length prefix, no CRC.
* `vendor.c:4-6` says why, and it forecloses the obvious alternative too: *"we
  support only a vendor-request based protocol, as we're trying to keep code size
  small… we want to avoid the overhead of the libgreat comms API."*

So GCP is not in the Apollo link and never was. It is in the tree only as
vendored upstream for Moondancer, it is request/response with no push path, and
it rides a USB control endpoint this SoC does not have
(`firmware/cynthion-soc/src/usb.rs:14-18`: *"This SoC has no USB device
controller."*).

### Apollo's CDC bridge — not a protocol, a wire

This is the half that answers "already the case, possible, or category error"
with **possible, and with no Apollo change at all**:

* `repos/apollo/firmware/src/console.c:116-132` moves UART ring bytes into
  `tud_cdc_write_char()` and CDC bytes out to `uart_nonblocking_write()`. Byte
  for byte, no interpretation.
* SERCOM2 on PA14/PA11 (`.../boards/cynthion_d11/uart.c:27,39-40,114-115`), which
  is the SoC's second UART (`gateware/soc/top.py:1612-1681`, `APOLLO_UART_BAUD =
  115200` at `:291`).

So records **can** ride the Apollo link today, because that link carries bytes
rather than records. Three properties make it a fallback rather than the path:

* **115200 8N1 → 11.5 kB/s**, against a USB CDC on the FPGA's own port.
* **The FPGA must never speak first.** T14 is JTAG TMS (`gateware/soc/top.py:181-199`,
  `firmware/cynthion-soc/src/target.rs:210-216`), so binary mode is opt-in per console.
* **An Apollo JTAG session stops it dead.** `console.c:106-108` returns early
  while JTAG is active and `jtag.c:19` takes the pinmux, so the stream truncates
  mid-frame with nothing said. SLIP plus `seq` is what turns that into a counted
  gap rather than a silent one.

### The sideband is a third thing, and also not this

Fixed-length commands with a trailing CRC-8, Apollo-mastered, *"lengths are fixed
and there is no framing"*
(`repos/apollo/firmware/src/boards/cynthion_d11/fpga_adv.c:463-465`), reached from
the host through vendor request `0xc3` (`vendor.c:388-435`). #137 cut it to
liveness and messaging deliberately; nothing here changes that.

**Nothing is shared with any of the three.** Console 0 is the transport, and the
record format is ours alone.

---

## Scope: smaller than this issue describes, on purpose

**The first slice should be read-only.** A reader duplicates no driver semantics,
so it needs none of #303's refactor to be correct. Writes do — a binary front end
that reimplemented `power::mv_to_limit_code` would reintroduce #270's 2× scale
bug independently. So the host-to-board surface here is **two raw bytes**: `0x03`
to leave binary mode, `?` to re-emit `SESSION` and the catalogue. Everything else
a host might want to *set* is a driver operation and waits for #303.

That decouples this issue's first producer from #303 entirely, which the earlier
plan did not.

## Cost

* **On the board today: zero.** Nothing here is firmware.
* The CRC is free — already linked.
* The only `.text` measurement that exists is the prototype quoted earlier in
  this issue (1,382 B `.text`, 372 B `.rodata`, `opt-level = "z"`, `lto = true`,
  `codegen-units = 1`). It used different framing and a different CRC from this
  spec, so it is an order of magnitude, not a number for this design.

## Two interactions to fix before a producer lands

* **`fmt::Write for Uart` inserts `\r` before every `\n`** (`uart.rs:452-458`).
  A record emitter must call `Uart::put` directly; `write!` would corrupt any
  body containing `0x0A`.
* **`soc_test.py` asserts no bare LF reaches the wire** (`uart.rs:450`). Binary
  bodies contain `0x0A` without a preceding `0x0D` by construction, so that check
  has to be scoped to text mode.

## Not established, and what would settle it

* **Console 0's real device-to-host byte rate is unmeasured.**
  `gateware/soc/top.py:1600` sets `serial.tx.last.eq(1)` — one USB IN transaction
  per byte, correct for a console and the ceiling for anything else. No record
  format gets under it. Settled by one measurement on the board; do it before
  quoting a sample rate, and before deciding whether a packing bit, a second CDC
  or bulk is needed.
* **The `.text` cost of the firmware emitter for *this* spec.** Settled by
  `./scripts/soc_text_budget.py` once it exists.
* **The live decode path has never run**, because nothing emits records.
