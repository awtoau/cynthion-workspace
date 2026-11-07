# The device interface — binary request/response ops

Coarse operations over the drivers, so the SoC's text front end (shell parsing,
help and message strings, `core::fmt`) can later be replaced by a simulated
shell on Linux ([#303](https://github.com/awtoau/cynthion-workspace/issues/303)).

- Host side: [`../scripts/soc/soc_ops.py`](../scripts/soc/soc_ops.py) (op table, codec),
  [`../scripts/cynthion_ops.py`](../scripts/cynthion_ops.py) (client).
- Frozen in [`../tests/test_soc_ops.py`](../tests/test_soc_ops.py) and
  [`../firmware/cynthion-soc-tests/tests/ops_golden.txt`](../firmware/cynthion-soc-tests/tests/ops_golden.txt).
- Firmware: [`../firmware/cynthion-soc/src/ops.rs`](../firmware/cynthion-soc/src/ops.rs) and `ops/`.
- Decision and scope: Dan, 2026-11-07, comments on #303. Steps 1-2 only; the text shell stays.

## Rules

- **Ops are coarse**: read a power channel, set a VBUS source, set an LED. Not register or I2C pass-through.
- **One op = one round trip.** The host holds no timing-critical state.
- **Sequencing, timing and safety stay on the SoC**: I2C sequences, FUSB302B FIFO and GoodCRC timing, PD timers, PAC limit and alert actions, USB engine timing, the VBUS gate.
- **Units and scales live in the driver** ([#270](https://github.com/awtoau/cynthion-workspace/issues/270)). Every value on the wire is mV, uA, ms, kHz or an enum. The host converts nothing, so a host copy of `power::mv_to_limit_code` cannot exist.
- **Drivers return numbers and enums**, never strings. Shell and ops call the same driver function; the shell renders, ops encode.
- **Raw register peek/poke** is an optional debug op, not in this round (`debug.peek`, planned).

## Framing

Reuses the record-stream frame of [`binary-protocol.md`](binary-protocol.md) byte for byte:

```
C0 | kind:u8  ver|flags:u8  len:u16  seq:u32  at:u32 | body[len] | crc32:u32 | C0
```

- SLIP, little-endian, CRC-32/ISO-HDLC over header + body, version nibble 1, `MAX_BODY` 256 (protocol limit).
- A host can join mid-stream: discard to the first `C0`, then frames.
- Two new kinds, in the stream's reserved range:

| kind | name | direction | `seq` | `at` | body |
|---|---|---|---|---|---|
| `0x08` | REQ | host to board | request id, host-chosen | 0 | `op:u16`, args |
| `0x09` | RSP | board to host | the request's id, echoed | board ms when the request frame completed | `op:u16 status:u8`, result or failure tail |

- **Request id** is any u32. Ids need not be unique or increasing; the host matches on the echo. One request outstanding at a time per session (the board answers in order and has one buffer).
- **RSP does not consume the stream counter.** Its `seq` is the echoed id, so the decoder excludes REQ/RSP from gap detection. Pushed records (TEXT, DROP, ERROR, later EVENT) keep the stream's own monotonic `seq`.
- **Events are pushed as records** of the existing kinds. This round: TEXT (async log lines diverted from the text path), DROP (text lost), ERROR (frames that never became requests, session end). EVENT (`0x20`) and producers come with the event ops.
- **Buffer limits** are reported by `sys.capabilities`: request body at most 64 bytes, response body at most 128 (firmware `.bss`, not protocol limits).

### Status and failure tail

`status`: `0` ok, `1` unknown_op, `2` bad_args (length or range), `3` refused (policy or gate said no), `4` unavailable (no such hardware on this target, or no data yet), `5` io (bus error).

- `status = 0`: result per the op table.
- `status != 0`: the result is always `reason:u8 value:u32`, 5 bytes. `reason` is op-specific (below), `value` the number that explains it (mV for a gate refusal), else 0.

`vbus.set` refusal reasons (status 3): 1 too_high, 2 too_low, 3 uv, 4 ov, 5 unreadable, 6 not_sampling, 7 not_output, 8 other_source_closed, 9 cc_failed.
Unavailable reasons (status 4, any op): 1 no_board (QEMU, or the op needs a peripheral this target lacks), 2 no_sample, 3 read_failed.

### Frames that never become requests

ERROR record (`kind 4`, body `code:u32 value:u32`), then dropped; no RSP, so the host sees a timeout and retries with a new id.

| code | name | cause |
|---|---|---|
| 1 | crc | CRC-32 mismatch |
| 2 | escape | malformed SLIP escape |
| 3 | runt | shorter than header + CRC |
| 4 | length | `len` disagrees with the frame, or exceeds the protocol limit |
| 5 | version | envelope version not 1 (checked after the CRC) |
| 6 | kind | not a REQ |
| 7 | overflow | frame longer than the board's request buffer; value = buffer size |
| 8 | short_request | REQ body under 2 bytes (no opcode) |
| 9 | left | session ended; value 1 host ETX, 2 watchdog |

Each bad frame costs one 21-byte ERROR out for at least 3 bytes in; noise on the line is bounded by the line rate.

## Binary mode: entry, exit, watchdog

- **Entry**: text command `ops` on a console. The board answers with a SESSION record (`RESYNC`) as the first frame; anything before the first `C0` is the text prompt and is discarded by the host. **The host waits for SESSION before sending a frame**: bytes sent earlier go to the line editor.
- **While in binary mode on a console**: its bytes bypass the line editor entirely; async text (log lines from tasks) is diverted into TEXT records, emitted by the one writer (idle context) between responses. One console at a time; `ops` on a second console while one is active replies `ops: busy` as text.
- **Exit, host**: one raw byte `0x03` (ETX), recognised only *between* frames (immediately after a `C0`, never inside one; a frame's first byte is its kind, `0x08`, so there is no clash). The board sends ERROR(`left`, 1), then the text line `ops: back to text`.
- **`?`** between frames: re-emit SESSION (the catalogue is empty; no producers this round).
- **Exit, watchdog**: the board leaves binary mode after **2500 ms** with no received byte and no response sent.
  - Waits for: any byte from the host. The client library sends `sys.ping` every `KEEPALIVE_MS` = 2000 ms when otherwise idle.
  - Expected: a live host speaks at least every 2000 ms (its keepalive period).
  - Multiplier: 1.25 x 2000 = 2500 ms.
  - Clock: reset on every received byte and after every response sent, so a long op cannot expire it.
  - On expiry: board sends ERROR(`left`, 2) (its leading `C0` aborts any partial frame the host is reading), then the text line `ops: watchdog, back to text`. The text shell is usable again after one Enter.
  - Why it exists: a host that dies mid-session must not leave the only diagnostic console speaking binary. It is `load`'s missing abort ([`binary-protocol.md`](binary-protocol.md) pitfalls).
- **No lock is held across the session.** Each op takes the `devices` lock for its own duration only, as a shell command does; `load`'s lock-for-the-transfer is not repeated.
- **Transport**: console 0 (USB CDC) now. The AUX link in device mode carries identical frames; nothing in the frame or the ops mentions the transport.

## Versioning and discovery

- Envelope version is the header nibble (1). Op-protocol version is `proto` in `sys.capabilities` (1).
- `sys.capabilities` returns limits, feature bits and the supported opcodes (paged, 48 per page). A host driver enables only what the list contains.
- Adding an op, or appending result fields, does not bump `proto`; a client must accept a result longer than it knows. Changing or removing a field does.
- Feature bits: 0 board peripherals present (false under QEMU), 1 HyperRAM peripheral.

## Op table

Generated by `./scripts/soc/soc_ops.py --table`; layouts are in `soc_ops.OPS` and are the single host declaration. A test compares the opcodes with the firmware constants.

| code | op | args | result | replaces | idem | worst case on the board |
|---|---|---|---|---|---|---|
| `0x0001` | `sys.ping` | nonce:u32 | nonce:u32 | - | yes | <1 ms |
| `0x0002` | `sys.capabilities` | first:u16 | proto:u8 max_req_body:u16 max_rsp_body:u16 features:u32 total:u16 n:u8, ops:u16 x n | - | yes | <1 ms |
| `0x0003` | `sys.info` | - | fw_git:u32 time_hz:u32 uptime_ms:u32 console:u8 consoles:u8 | `info` | yes | <1 ms |
| `0x0100` | `power.read` | - | age_ms:u32 n:u8, per channel: mv:u32 ua:i32 floor_ua:u32 flags:u8 | `pac1954 status`, rail line of `usb <port>` | yes | ~3 ms |
| `0x0101` | `power.limits` | channel:u8 | ov_mv:i32 uv_mv:i32 oc_ua:i32 uc_ua:i32 armed:u8 fired:u8 | `pac1954 limit`, `alert` (read) | yes | <1 ms |
| `0x0200` | `vbus.status` | - | raw:u8 closed:u8 powered:u8 | `vbus`, `usb <port> power` | yes | <1 ms |
| `0x0201` | `vbus.set` | port:u8 action:u8 current_ma:u16 | source:u8 mv:u32 flags:u8 | `vbus <src>`, `vbus off`, `vbus charge`, `usb <port> power on\|off` | yes | ~5 ms |
| `0x0300` | `led.get` | - | lit:u8 fabric:u8 button:u8 | `led`, `info button` | yes | <1 ms |
| `0x0301` | `led.set` | led:u8 state:u8 | lit:u8 fabric:u8 | `led <colour> on\|off\|fabric` | yes | <1 ms |
| `0x0400` | `typec.status` | - | configured:u8 lines:u8, per port: ok device_id vbus cc1 cc2 source_cc orientation (u8) serviced:u32 | `fusb302b` | yes | ~3 ms |
| `0x0401` | `pd.status` | - | armed cc running ready (u8) sent received errors hardresets (u32) contract_pos:u8 contract_ma:u16 session_bytes:u32 | `pd target_c status` | yes | <1 ms |
| `0x0500` | `usb.port` | port:u8 | sample:u8 mv:u32 ua:i32 drawing switch cable vbus cc1 cc2 source_cc orientation (u8) | `usb <port> status` | yes | ~3 ms |
| `0x0600` | `hr.status` | - | id ck_khz_req ck_khz_meas (u32) ready locked calibrated busy hr_busy last_op last_code (u8) errors state (u16) cal_tap cal_width cal_valid (u8) | `hr status` | yes | <1 ms |
| `0x0700` | `selftest.run` | - | passed failed skipped n (u8), n x (item:u8 outcome:u8) | `selftest` | yes | unmeasured |

Wire codes (stable, independent of firmware table order):

- **Channels** (`power.read` order, `usb.port`/`vbus.set` `port`): 0 target_a, 1 target_c, 2 aux, 3 control. The PAC1954's order, not connector order ([`power.rs`](../firmware/cynthion-soc/src/power.rs) `PORTS`).
- **LEDs**: 0 red, 1 orange, 2 yellow, 3 green, 4 blue, 5 violet. Masks use bit n = LED n. States: 0 off, 1 on, 2 fabric (released to the gateware).
- **vbus.set action**: 0 power off `port`, 1 power on `port` (`current_ma` 0, 1500 or 3000 = Rp advertised, target_c only), 2 close `port` as the rail source (control, aux or target_c; `vbus control` etc.), 3 open every switch (`port` ignored).
  - `source` in the result: 0 control, 1 aux, 2 target_c, `0xFF` none (off, open-all). `flags` bit 0 fell back to aux, bit 1 already on (`mv` 0).
  - On a target without the I2C bus, power-on and close-source are unavailable (no_board); off and open-all still act, as the shell's do.
  - Goes through the existing gate: the PAC1954 read and UV/OV flags decide, not the host ([`vbus_gate.rs`](../firmware/cynthion-soc/src/vbus_gate.rs)).
  - Power on/off arms and disarms the PD session on target_c, as the shell does.
- **power.read flags**: bit 0 drawing (|current| at or above the channel floor); the driver decides, not the host.
- **orientation**: 0 unknown, 1 unattached, 2 cc1, 3 cc2, 4 both. `cc1`/`cc2` are Rd bands 0..3; `0xFF` not measured.
- **usb.port**: `switch` 0 none, 1 open, 2 closed; `cable` 1 when the cached Type-C state is valid, else 0 (cc fields then 0xFF). `sample` 0 = no sample; `mv`/`ua` then 0.
- **selftest items**: 0 alu, 1 muldiv, 2 comp, 3 atomic, 4 ram, 5 clock, 6 uart, 7 gateware, 8 flash, 9 phy, 10 rail. Outcome 0 pass, 1 fail, 2 skip. An item repeats per console or rail.

Idempotence: every op in this round is safe to repeat. `vbus.set` on an already-on port returns `already` and closes nothing. Ops that are not (planned `sys.reset`, `flash.erase`, `pd.send`) will say so in this column.

### Timeouts

- Client limit per op = 1.25 x the worst case above, floor 50 ms for the USB CDC round trip. On expiry the client raises with op, limit and elapsed; it never retries silently.
- The board-side times are estimates from the driver's own bounds (`power.rs` `ON_DEMAND_WAIT_US` 1.25 ms, a 16-byte I2C read). They are not measured; the bench plan below measures them and the table is then corrected.
- `selftest.run` is unmeasured. The client uses 250 ms provisionally.

## Every shell command, and its op

Legend: **done** = op in the first set; **planned** = op named, next round; **text** = not an op.

| shell command | op |
|---|---|
| `board` | text: composite view; the host composes it from `power.read`, `vbus.status`, `typec.status`, `usb.port` |
| `bram`, `bram status` | text: static facts, host holds them |
| `bram read <hex>`, `hyperram read <hex>`, `flash read <hex>` | planned `debug.peek` (optional raw word read) |
| `bram bench`, `flash bench`, `bench ...` | text: timing diagnostics |
| `cpu`, `cpu stats` | planned `cpu.stats` |
| `cpu check`, `cpu stress`, `cpu irq`, `cpu wedge`, `cpu fault` | text: bring-up and fault-injection tools |
| `cpu log [n\|tags]`, `cpu status` | planned: ring drained to EVENT records, `cpu.status` |
| `flash`, `flash status`, `flash sfdp` | planned `flash.status` |
| `flash erase <hex>`, `flash program <hex>` | planned `flash.erase`, `flash.program` (destructive, not idempotent) |
| `fusb302b [port]` | done `typec.status` |
| `help`, `?` | text: help lives in the host shell |
| `hr`, `hr status` | done `hr.status` |
| `hr abort`, `calibrate`, `delay`, `errors`, `reg`, `results` | planned `hr.*` |
| `hr fill`, `hr test`, `hr inject`, `hr read`, `hr write` | text: lab generators and raw access (`debug.peek` for the reads) |
| `hyperram` | text |
| `i2c`, `i2c status`, `i2c scan`, `i2c soak` | text: no I2C pass-through by decision |
| `info` | done `sys.info` (gateware id not yet carried) |
| `info map`, `info pmod` | text: static tables, host holds them |
| `info ports` | planned `sys.ports` |
| `info button` | done, `led.get` field `button` |
| `init`, `init <peripheral>` | planned `sys.init` |
| `led [colour]`, `led <colour> on\|off\|fabric` | done `led.get`, `led.set` |
| `load <hex>` | text: a transfer mode, not a request; planned `fw.stage` with chunked ops and per-chunk CRC |
| `pac1954`, `pac1954 status` | done `power.read` (+ `power.limits`) |
| `pac1954 alert` | done `power.limits` fields `armed`, `fired` |
| `pac1954 floor <port> <mA>` | planned `power.floor.set` |
| `pac1954 rate`, `detect`, `hispeed` | planned `power.rate`, `power.detect`, `power.hispeed` |
| `pac1954 limit`, `samples`, `bracket` | planned `power.limit.set` (units converted on the board) |
| `pac1954 reset` | planned `power.reset` (destructive: loses accumulators) |
| `pd`, `pd target_c [status]` | done `pd.status` |
| `pd target_c caps`, `log`, `regs` | planned `pd.caps`, `pd.events` |
| `pd target_c live` | text: console echo toggle; events replace it |
| `pd target_c list` | text: the host holds the verb names |
| `pd target_c send <name>`, `raw <hex>` | planned `pd.send` (PD message send, reply and events) |
| `reset` | planned `sys.reset` (not idempotent) |
| `rtic` | text: scheduler diagnostics |
| `selftest` | done `selftest.run` |
| `sideband` | text for now |
| `time` | partly `sys.info` (`uptime_ms`, `time_hz`) |
| `time set <epoch>` | planned `sys.set_time` |
| `usb`, `usb aux`, `usb control`, `usb target_a`, `usb target_c` | done `usb.port` (`usb <port> status`) |
| `usb <port> power [on\|off]` | done `vbus.status` (read), `vbus.set` |
| `usb <port> role`, `reset`, `desc`, `setup`, `in`, `out`, `addr`, `aoa`, `pump`, `echo` | planned `usb.host.*`: control and bulk transfers; engine timing stays on the SoC |
| `usb <port> tx`, `rx` | planned `usb.raw.*` |
| `usb3343`, `usb3343 status`, `usb3343 reset` | planned `phy.status`, `phy.reset` |
| `vbus`, `vbus status` | done `vbus.status` |
| `vbus off` | done `vbus.set` action 3 |
| `vbus control\|aux\|target_c` | done `vbus.set` action 2 |
| `vbus charge [1.5\|3]` | done `vbus.set` action 1 on target_c with `current_ma` |
| `ops` | new: enters binary mode |

Hidden aliases (`power`, `typec`, `phy`, `workload`) dispatch to the same families and map as above.

Counts: 14 ops implemented, covering 24 of the 97 `HELP` rows (`ops` included); the planned rows are listed as planned; the rest are text-only by decision.

## Test strategy

- **Golden frames**: [`ops_golden.txt`](../firmware/cynthion-soc-tests/tests/ops_golden.txt) is generated by `soc_ops.py --golden`; the Python test fails if it differs from the generator, the Rust test replays every request through the real dispatcher with a fake driver and requires the exact response bytes. A changed byte on either side fails.
- **Host Rust tests** ([`../firmware/cynthion-soc-tests/tests/ops.rs`](../firmware/cynthion-soc-tests/tests/ops.rs)): framing, each op's argument and result codec, malformed cases, watchdog with an injected clock.
- **QEMU gate** ([`../scripts/soc/soc_ops_gate.py`](../scripts/soc/soc_ops_gate.py), called by `scripts/soc_test.py`, on an emulator of its own): enter, every op, bad arguments; malformed, overlong, truncated, bad-CRC, bad-escape, wrong-version, wrong-kind and unknown-opcode frames; 80% of the watchdog in silence keeps the session, a stuck host mid-frame is cut loose at 2.5 s; ETX; the text shell afterwards. Not covered: async text diverted into TEXT records (QEMU logs nothing unprompted) and anything that needs the board.
- **Cannot-fail proofs**: the Rust golden test is re-run with a driver value shifted per op and must differ (`cannot_fail_proof_each_golden_comparison_catches_a_changed_value`); a changed byte at every frame position is caught by the CRC test; the watchdog test asserts 2499 ms stays and 2500 ms leaves, across a clock wrap; the Python golden test asserts a one-byte change is detected. The QEMU gate was run once against a deliberately wrong reason code and failed 7 checks.

## Bench path (not built this round)

The board arbiter ([`board-arbiter.md`](board-arbiter.md)) speaks the text shell and attributes replies by the prompt. A binary job needs:

1. A job type `ops` that sends `ops\r`, waits for the SESSION frame, runs a list of (op, args) from a file, and exits with ETX. The client library does framing, ids, timeouts and the 2000 ms keepalive.
2. The transcript is the hex frames plus the decoded ops, with provenance as for text jobs. Responses carry the request id, so reply attribution ([#632](https://github.com/awtoau/cynthion-workspace/issues/632)) is by construction.
3. After every `ops` job, whatever its exit: send `\r` and require the text prompt within 2500 ms + 250 ms (watchdog + one round trip). A job that crashed mid-frame is recovered by the watchdog, not by the arbiter.
4. The arbiter must not interleave a text job on a console that is in binary mode; it already serialises jobs.
5. First bench run: measure every worst case in the table, and `selftest.run`, and correct the table.
