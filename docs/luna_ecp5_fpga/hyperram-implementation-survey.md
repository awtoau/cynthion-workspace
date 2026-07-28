# HyperRAM implementations: what else is out there

A survey prompted by reasonable scepticism about LUNA's implementation, after
Glasgow's QSPI controller turned out to be markedly better than what was being
written by hand for the flash.

**Conclusion: LUNA's is the one to keep**, but with a specific, measurable
inefficiency worth fixing — and one alternative worth borrowing ideas from.

## Candidates found

| Project | Language | Licence | Last touched | Verdict |
|---|---|---|---|---|
| **LUNA** `HyperRAMInterface` | Amaranth | BSD-3 | in use | **Keep.** ECP5 DDR primitives, verified working here |
| **ChipFlow** `chipflow-digital-ip` | Amaranth | BSD-2 | Jan 2026 | Borrow ideas; no FPGA I/O |
| Squishy | Amaranth | BSD-3 | Jan 2026 | Empty stub, 27 lines |
| litex-hyperram | Migen | **none** | Dec 2019 | Unlicensed, 7 years stale |
| orbtrace | Migen | — | — | Wraps `litehyperbus`, not standalone |

Glasgow has **no** HyperRAM support, so the trick that worked for QSPI does not
repeat here.

## Why LUNA wins despite the scepticism

The decisive point is that LUNA instantiates real ECP5 DDR hardware —
`DQSBUFM`, `TSHX2DQSA`, `DDRDLLA`, `ODDRX1F`, `DELAYF`. HyperRAM is a DDR
interface, and on an FPGA that means vendor I/O primitives; you cannot write it
portably.

ChipFlow's is the better-*structured* code by some distance, but it is written
for ASIC targets and contains **no FPGA I/O primitives at all** — no
`DDRBuffer`, no `Instance()`. Adapting it would mean writing the ECP5 DDR layer
from scratch, which is precisely the part LUNA already has working.

And LUNA's is verified on this board: 32 KiB bulk write/read, retention across
~6 ms, and 4096 random-address operations, all with **zero errors** at 120 MHz
(`ecp5-test/hyperram/`).

## What ChipFlow does better

Two things worth taking:

**Latency is a runtime CSR, not a constant.** ChipFlow exposes `latency` as a
4-bit read/write register field. LUNA hard-codes `LOW_LATENCY_CLOCKS = 7` and
`HIGH_LATENCY_CLOCKS = 14`.

**It has the Wishbone peripheral already.** ChipFlow's `data_bus` is a 32-bit
Wishbone interface with byte granularity, plus a separate CSR control bus —
exactly the wrapper that is missing on the LUNA side, and the reason the
HyperRAM path stalled while the flash path did not.

## The concrete inefficiency in LUNA

`HyperRAMInterface` samples RWDS correctly to detect whether the device is
asking for extra latency:

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

This is **conservative rather than wrong** — it is an acknowledged shortcut with
a FIXME against it, and taking the longer latency is always safe. But it costs
about **7 cycles on every transaction**, which matters here specifically:
measured overhead is ~23 cycles per transaction against ~1 cycle per word, so
this is roughly 30% of the fixed cost that makes random access ~25 cycles
against ~2 for streaming.

Fixing it is a one-line change plus a test. Whether it is worth doing depends on
the access pattern: irrelevant for streaming FIFO use, worth having if anything
does small scattered accesses.

## Recommendation

1. **Keep LUNA's `HyperRAMInterface`.** The ECP5 DDR work is the hard part, it
   is done, and it is verified on this hardware.
2. **Write the Wishbone wrapper**, using ChipFlow's `data_bus` shape as the
   reference — 32-bit with byte granularity, separate CSR control.
3. **Consider honouring RWDS** rather than always taking high latency, but
   measure the gain first. It is ~7 cycles per transaction and therefore
   invisible in FIFO use.
4. **Do not adopt ChipFlow wholesale.** Better structure does not outweigh
   having to write ECP5 DDR I/O from scratch.

## Taking the best of both

The two projects are strong in **non-overlapping** places, so this is a stack
rather than a merge — nothing has to be rewritten or reconciled:

| Layer | LUNA | ChipFlow | Take |
|---|---|---|---|
| ECP5 DDR I/O primitives | yes | **none** | LUNA |
| HyperBus protocol FSM | yes | yes | LUNA — verified on this board |
| Latency as runtime CSR | no | yes | ChipFlow |
| 32-bit Wishbone data bus | no | yes | ChipFlow |
| CSR control registers | no | yes | ChipFlow |

LUNA owns everything below the protocol; ChipFlow owns everything above it.

Concretely, the plan is:

1. **Keep `HyperRAMInterface` and `HyperRAMPHY` untouched.** They carry the ECP5
   DDR work and they are the part verified here.
2. **Write a Wishbone wrapper on top**, shaped after ChipFlow's `data_bus`:
   32-bit, byte granularity, with a separate CSR bus for control.
3. **Make latency a CSR field** rather than a constant, as ChipFlow does. That
   also gives somewhere to put the RWDS fix if it proves worthwhile.

Note that what is being taken from ChipFlow is the **interface shape**, not the
code — it has no ECP5 layer, so there is nothing in it that would function here
even if copied verbatim. That makes it a design reference rather than a
dependency, and sidesteps the attribution question almost entirely. Licences
are compatible regardless: BSD-2 into a BSD-3 codebase is fine.

## Method note

This survey looked beyond GitHub as a matter of habit, but the useful results
were all on GitHub in this case. The search that mattered was for *Amaranth*
implementations specifically — Migen and LiteX ones exist but would need porting,
and the two that turned up were an ASIC-targeted design and an empty stub.
