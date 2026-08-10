# Byte and word order through the 32-bit DQS path

The convention [#206](https://github.com/awtoau/cynthion-workspace/issues/206)
asked for, measured rather than inferred. Measured 2026-08-10 in simulation
against [`hyperram_model.v`](../../../gateware/probes/hyperram/hyperram_model.v);
never yet confirmed on the board.

## The convention

**Big-endian throughout: the lower device address is the more significant end of
the fabric word.**

| fabric bit field | device word | half of it | wire edge |
|---|---|---|---|
| `dq[31:24]` | `A + 0` | high byte | first |
| `dq[23:16]` | `A + 0` | low byte | second |
| `dq[15:8]`  | `A + 1` | high byte | third |
| `dq[7:0]`   | `A + 1` | low byte | fourth |

Identical in both directions — `phy.dq.o` on a write, `phy.dq.i` on a read.
`A` is the device **word** address of the beat.

Concretely: `write_data = 0x12345678` puts `0x1234` at `A` and `0x5678` at `A+1`.

## What a CPU store does

`BootRAM` applies no transform on the 32-bit path
([`gateware/soc/bootram.py`](../../../gateware/soc/bootram.py),
`psram.write_data.eq(live_data)`), and `HyperRAMWishbone` passes `dat_w`/`dat_r`
through unchanged, so a RISC-V `sw` of value `V` lands as:

| store | Wishbone `sel` | device byte address | note |
|---|---|---|---|
| `V[7:0]`   | `sel[0]` | `2A + 3` | the LOWEST CPU byte address, at the HIGHEST device byte address |
| `V[15:8]`  | `sel[1]` | `2A + 2` | |
| `V[23:16]` | `sel[2]` | `2A + 1` | |
| `V[31:24]` | `sel[3]` | `2A + 0` | |

- A little-endian 32-bit word is stored **byte-reversed** in device byte space.
- A `hr ramp`-style pattern written as 32-bit words and read back byte-wise by
  any other agent will look reversed within each 4-byte group. That is this
  convention, not a fault.
- The 16-bit path uses the OPPOSITE word order at the same `BootRAM` boundary —
  see below.

## Is it unusual, and is it cheaper?

- **Byte order: not unusual.** HyperBus sends the most significant byte of a
  16-bit word first, and the CA is most-significant-byte-first too. The PHY's
  `i_D0 = dq.o[31:24]` makes the CA a plain contiguous slice
  (`phy.dq.o.eq(ca[16:48])` in
  [`hyperram_dqs_controller.py`](../../../gateware/soc/peripherals/hyperram_dqs_controller.py)).
- **Gate cost is zero either way** — the phase-to-bit map in
  [`hyperram_dqs_phy.py`](../../../gateware/soc/peripherals/hyperram_dqs_phy.py)
  is wiring. What the current order buys is that **no byte reversal is needed
  anywhere on the CA path**; any other order needs one built into `ca`.
- **Word order is the unusual part, relative to the rest of the SoC.** The
  non-DQS 16-bit path in `BootRAM` sends `live_data[15:0]` as the
  lower-addressed device word and reassembles reads as `Cat(staged_low,
  read_word)` — first word in the LOW half. The two paths are opposite. Neither
  is wrong on its own; a build that switched between them without a transform
  would see every 32-bit value half-swapped.

## How it was measured

    scripts/hyperram_dqs_model_sim.py --stage order

[`dqs_order_tb.sv`](../../../gateware/probes/hyperram/dqs_order_tb.sv), three
tests, none of which compares a path against itself. Log:
`tmp/logs/hyperram_dqs_model_sim.log`.

1. **CA.** The CA leaves through the same `dq.o` and the same ODDRX2DQA mapping
   the data does, and the device decodes an address out of it. Address
   `0x2ce1d6` gives four distinct CA bytes `a0 05 9c 3a`. Only the wiring as
   written resolves back to it:

   | data-path order | device resolved |
   |---|---|
   | as wired | `0x2ce1d6` — MATCH |
   | bytes swapped inside each half | `0x01d4e0` |
   | 16-bit halves swapped | `0x150028` |
   | full reverse | `0x202d00` |

2. **Write, checked against the model's array.** `0x12345678` and `0xa5c31234`
   written by the controller, then `u_ram.memory` read hierarchically — nothing
   on the read path takes part, so a write-side permutation has nothing to
   cancel against. Both patterns, latency codes 0 and 2, ten `latency_clocks`
   settings: `memory[+0] = 1234`/`a5c3`, `memory[+1] = 5678`/`1234`.

3. **Read, from a preloaded array.** `memory[BASE + k] = {2k, 2k+1}` — device
   byte address `j` holds `j` over 128 words, written directly into the model.
   Every byte in the window is distinct, so `read_data` names its own device
   byte offsets — four contiguous bytes in ascending order at every latency
   setting: `0x03040506` at the default shim offsets, `0x00010203` at
   `--rd-slip 1`. Ascending is the order; where the run of four starts is the
   slip, and it is reported separately.

### The patterns, and why they discriminate

- All four bytes distinct and both 16-bit halves distinct, in every pattern, so
  each of the 24 byte permutations produces a different observation and the one
  seen names exactly one order. The runner **checks** this rather than asserting
  it, and fails a pattern that repeats a byte.
- A ramp in WORDS (`0x1000 + k`, which
  [`dqs_model_tb.sv`](../../../gateware/probes/hyperram/dqs_model_tb.sv) uses for
  its own question) does NOT discriminate byte order: its high byte is `0x10` in
  every word.

### The negative control

Every run also rewires the DATA beats halves-swapped — the transform removed
from `BootRAM` in [#206](https://github.com/awtoau/cynthion-workspace/issues/206)
— with the CA left alone, so the device still answers the right address. All
four checks must fire. If they do not, the run fails: a check that cannot see
the fault it names has measured nothing.

## What this does NOT establish

- **ODDRX2DQA's own `D0..D3` to wire order.** No open model of the primitive
  exists; the behavioural PHY in the testbench IS
  [`hyperram_dqs_phy.py`](../../../gateware/soc/peripherals/hyperram_dqs_phy.py)'s
  mapping restated. The CA test is what constrains it — the CA and the data
  share the mapping, and only one orientation decodes an address — but it
  constrains the pair, not the primitive alone.
- **The board.** Simulation only. The staging observation quoted in
  [#206](https://github.com/awtoau/cynthion-workspace/issues/206) (`a5c31234`
  putting `a5c3` in the lower word without the swap) agrees with the word order
  above; its second word was corrupt, which is
  [#186](https://github.com/awtoau/cynthion-workspace/issues/186)'s territory,
  not this document's.
- **Where the read grouping is anchored.** At the testbench's default shim
  offsets the four bytes arrive 3 device bytes into the addressed word; at
  `--rd-slip 1` they arrive at +0 and `read_data` is `0x00010203`. The order
  signature is `(0, 1, 2, 3)` at every slip, which is what says the two are
  separate questions. The anchor is DQSBUFM's and belongs to
  [#186](https://github.com/awtoau/cynthion-workspace/issues/186).
- **The non-DQS 16-bit path's byte order on the wire.** Only `BootRAM`'s half
  ordering was read out of the source above; the 16-bit controller's own
  byte order was not measured here.

## The check that keeps it

`ORDER_WRITE` and `ORDER_READ` in
[`scripts/hyperram_dqs_model_sim.py`](../../../scripts/hyperram_dqs_model_sim.py).
A change to either is a change to the wire format: the run fails and names this
file, rather than reporting a new convention quietly.
