# PD stack on TARGET_C

Decision (Dan, 2026-11-07): [elagil/usbpd](https://github.com/elagil/usbpd) policy engine + [okhsunrog/fusb302b](https://github.com/okhsunrog/fusb302b) PHY replace the hand-written engine ([#633](https://github.com/awtoau/cynthion-workspace/issues/633)). Research: [README.md](README.md).

## Layout

| Piece | Where | Role |
|---|---|---|
| `usbpd` 3.0.0, `usbpd-traits` 2.0.0 | crates.io via `Cargo.lock` | source policy engine, protocol layer |
| `fusb302b` | [`firmware/fusb302b`](../../firmware/fusb302b) (`[patch.crates-io]`) | PHY; upstream 0.2.0 plus the source-role patch below |
| [`pd_lib.rs`](../../firmware/cynthion-soc/src/pd_lib.rs) | firmware | poll executor, `Bus`-backed async I2C, INT_N pin, `embassy-time` driver, DPM, raw exchanges |
| [`pd.rs`](../../firmware/cynthion-soc/src/pd.rs), [`pd/send.rs`](../../firmware/cynthion-soc/src/pd/send.rs) | firmware | attach, VBUS, status/log; `send`/`raw` |
| [`pd_decode.rs`](../../firmware/cynthion-soc/src/pd_decode.rs), [`pd_verbs.rs`](../../firmware/cynthion-soc/src/pd_verbs.rs) | firmware, host-tested | frame model and decode; named verbs |
| [`messages.toml`](messages.toml) | docs | machine-readable catalogue read by [`scripts/pd_exercise.py`](../../scripts/pd_exercise.py) |

## fusb302b patch

- Upstream is a sink PHY: init resets, forces Rd, needs a source's Rp.
- `Fusb302b::attach_configured`: no reset, no CC detect; the caller's CC/roles/`AUTO_CRC`/retries stay ([`fusb302::pd_start`](../../firmware/cynthion-soc/src/fusb302.rs)). Enables SOP'/SOP'' RX.
- `transmit_sop` / `receive_sop`: SOP' sync set 1,1,3,3 and SOP'' 1,3,1,3 on TX; the RX token is returned. `Driver::receive` still delivers SOP only.
- Tests: `firmware/fusb302b/tests/driver.rs` (3 added).

## Executor

- Session future in a static slot (size asserted), polled from the main loop with a no-op waker; INT_N is read from the mux lines.
- Raw exchanges cancel `engine.run()` at a clean await, use the PHY directly, then resume `run()`; only when the engine is idle in Ready and the wire has been quiet 35 ms.
- All frames get MessageIDs from one wire counter, so engine and raw frames never repeat an ID.

## Shell

- `pd target_c status|caps|log|live|regs|list`
- `pd target_c send <name>[!] [svid] [--force] [--illegal]` prints a `pdx` block: outcome, latency, sent frame, decoded replies.
- `pd target_c raw <hex> [sop|sop1|sop2]`: header + payload bytes; MessageID, roles and revision are overwritten.
