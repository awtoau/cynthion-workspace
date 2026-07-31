# HyperRAM implementations: what else is out there

**Conclusion: keep LUNA's**, with a specific measurable inefficiency worth
fixing, and one alternative worth borrowing interface shape from.

## Candidates found

| Project | Language | Licence | Last touched | Verdict |
|---|---|---|---|---|
| **LUNA** `HyperRAMInterface` | Amaranth | BSD-3 | in use | **Keep.** ECP5 DDR primitives, verified working here |
| **ChipFlow** `chipflow-digital-ip` | Amaranth | BSD-2 | Jan 2026 | Borrow ideas; no FPGA I/O |
| Squishy | Amaranth | BSD-3 | Jan 2026 | Empty stub, 27 lines |
| litex-hyperram | Migen | **none** | Dec 2019 | Unlicensed, 7 years stale |
| orbtrace | Migen | — | — | Wraps `litehyperbus`, not standalone |

Glasgow has **no** HyperRAM support. The search that mattered was for *Amaranth*
implementations specifically; Migen and LiteX ones exist but would need porting.

## Why LUNA wins

LUNA instantiates real ECP5 DDR hardware. HyperRAM is a DDR interface, and on an
FPGA that means vendor I/O primitives; it cannot be written portably.

The two PHYs use different primitives, which matters because only one of them
works on this board:

| PHY | primitives | on Cynthion r1.4 |
|---|---|---|
| non-DQS (`HyperRAMPHY`) | `ODDRX1F`, `IDDRX1F`, `DELAYF` | **the verified path** |
| DQS (`HyperRAMDQSPHY`) | `DQSBUFM`, `TSHX2DQSA`, `DDRDLLA` | unusable — no DQS pin group |

Every measurement in `hyperram-speed.md` is on the non-DQS path.

ChipFlow's is the better-*structured* code by some distance, but it targets ASICs
and contains **no FPGA I/O primitives at all** — no `DDRBuffer`, no `Instance()`.
Adapting it means writing the ECP5 DDR layer from scratch, which is exactly the
part LUNA already has working.

LUNA's is verified on this board: 32 KiB bulk write/read, retention across ~6 ms,
and 4096 random-address operations, all with **zero errors** at 120 MHz
(`ecp5-test/hyperram/`).

## What ChipFlow does better

- **Latency is a runtime CSR**, a 4-bit read/write register field. LUNA
  hard-codes `LOW_LATENCY_CLOCKS = 7` and `HIGH_LATENCY_CLOCKS = 14`.
- **The Wishbone peripheral already exists.** ChipFlow's `data_bus` is a 32-bit
  Wishbone interface with byte granularity, plus a separate CSR control bus —
  exactly the wrapper missing on the LUNA side.

## The concrete inefficiency in LUNA

`HyperRAMInterface` samples RWDS correctly to detect whether the device is asking
for extra latency:

    m.d.sync += extra_latency.eq(self.phy.rwds.i)

and then discards the result:

    # FIXME: our HyperRAM part has a fixed latency, but we could need to detect
    # different variants from the configuration register in the future.
    with m.If(extra_latency | 1):
        m.d.sync += latency_clocks_remaining.eq(self.HIGH_LATENCY_CLOCKS-2)
    with m.Else():
        m.d.sync += latency_clocks_remaining.eq(self.LOW_LATENCY_CLOCKS-2)

`extra_latency | 1` is unconditionally true, so the low-latency branch is dead
code and every transaction takes the 14-clock path.

This is conservative rather than wrong — an acknowledged shortcut with a FIXME
against it, and the longer latency is always safe. It costs about **7 cycles per
transaction**.

Measured per-transaction overhead is **20 cycles for a write and 26 for a read**
at 120 MHz, constant across chunk sizes (`hyperram-speed.md`). So the fixed
latency shortcut is roughly a third of that overhead. Against a 512-byte
transfer it disappears into the noise; against a single word it is most of the
cost.

Fixing it is a one-line change plus a test. Irrelevant for streaming FIFO use;
worth having if anything does small scattered accesses.

## Plan

The two projects are strong in non-overlapping places, so this is a stack rather
than a merge:

| Layer | LUNA | ChipFlow | Take |
|---|---|---|---|
| ECP5 DDR I/O primitives | yes | **none** | LUNA |
| HyperBus protocol FSM | yes | yes | LUNA — verified on this board |
| Latency as runtime CSR | no | yes | ChipFlow |
| 32-bit Wishbone data bus | no | yes | ChipFlow |
| CSR control registers | no | yes | ChipFlow |

1. **Keep `HyperRAMInterface` and `HyperRAMPHY` untouched.**
2. **Write a Wishbone wrapper on top**, shaped after ChipFlow's `data_bus`:
   32-bit, byte granularity, separate CSR bus for control.
3. **Make latency a CSR field** rather than a constant. That also gives somewhere
   to put the RWDS fix if it proves worthwhile — measure the gain first.
4. **Do not adopt ChipFlow wholesale.**

What is taken from ChipFlow is the **interface shape**, not the code — it has no
ECP5 layer, so nothing in it would function here even if copied verbatim. That
makes it a design reference rather than a dependency. Licences are compatible
regardless: BSD-2 into a BSD-3 codebase is fine.
