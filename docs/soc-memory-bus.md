# The SoC's memory bus: is Wishbone the wrong choice?

Was `hyperram-bus-review.md`. The subject is the SoC's interconnect -- Wishbone
against AXI4 and TileLink, and what `RegisteredResponse` costs -- with HyperRAM
as the case that forces the question. It is kept whole rather than split because
it is one argument: the part cannot stall, therefore the bubble is inherent,
therefore here are the four ways out, ranked.

**No. The bus flavour is not the fault, and changing it would not have helped.**
The fault is that a HyperBus data phase has no backpressure and every bus this
SoC could plausibly use — Wishbone classic, Wishbone B4, AXI4, TileLink — allows
the master to stop accepting. Something has to absorb that mismatch, and today
nothing does.

**And there is a documented way to make the device stall.** HyperBus calls it
**Active Clock Stop**, the W956A8 supports it by name, and luna's PHY already has
the gate wired. That makes §5 the recommendation and most of §1–§4 moot.

**It is now built and it simulates clean**: 16/16 beats both directions, 32
device words, one transaction per line, against the 8/16 and 48 words the board
measures without it. `ClockStopPHY` in `gateware/soc/bootram.py`,
checked by §11 of `scripts/soc_hyperram_sim.py`, and **off by default** — see
"The smallest experiment, run" below for what is settled and what the model
cannot settle, which is the read path's round-trip latency.

**Index:** [`chips/w956a8-hyperram.md`](chips/w956a8-hyperram.md) ·
[`upstream-boundary.md`](upstream-boundary.md) ·
[`riscv-core-build.md`](riscv-core-build.md)

Everything below is marked **measured** (a number this workspace produced),
**read from source** (a file and line), **cited** (a datasheet clause), or
**inferred**. Where a question cannot be settled it says so.

## What is on the table

**Measured**, `scripts/soc_hyperram_sim.py` §8, re-run 2026-08-05 on `016f4c6`,
all checks pass. Device CK = `SYNC_MHZ` = 60 MHz, since `HYPERRAM_DQS = False`
and `HyperRAMPHY`'s `ODDRX1F` emits one CK per `sync` cycle:

| arrangement | CK per 64-byte line | device-side ceiling |
|---|---|---|
| coalesced, one transaction | **49** (17 overhead + 32 words) | 78.4 MB/s |
| `sustained=False`, one transaction per beat | **304** (16 × 19) | 12.6 MB/s |

6.20x on the device side. **Measured** on the board, the same change cost less
than that because the CPU-side path dominates — pre-fix figures from `a7e4147`,
post-fix from the review brief and not in git as of `016f4c6`:

| walk | coalescing (corrupt) | per beat (correct) | lost |
|---|---|---|---|
| 16 KiB read seq | 10.96 MB/s | 5.43 MB/s | 2.02x |
| 16 KiB read seq, four-deep | 20.70 MB/s | 6.98 MB/s | 2.97x |

So the prize is **2–3x measured, 6.2x on the device side**, and the gap between
those two numbers is the second half of the answer: see §4.

Those left-hand figures were taken on a *corrupt* burst, which is worth stating
rather than apologising for: the corrupt arrangement did **more** device work
than a correct one — 48 words per line written where 32 are needed, three words
advanced per beat read where two are — so 10.96 and 20.70 MB/s are a **floor** on
what correct coalescing gives, not an optimistic reading of it.

**One fact worth having straight before anything else.** `RegisteredResponse`
landed 2026-08-01 in `d440f03`; burst coalescing landed 2026-08-03 in `acfaa5d`.
Coalescing has never once worked in this SoC. `7351eb9`'s "the controller is idle
79% of a cache line" and its 4.83x headroom estimate were taken from the corrupt
burst and do not describe anything reachable.

## 0. The constraint, restated — and what it rules out

`HyperRAMInterface`'s `WRITE_DATA` asserts `write_ready` unconditionally
(`luna/gateware/interface/psram.py:277`, `m.d.comb += self.write_ready.eq(1)`),
and `READ_DATA` asserts `read_ready` on every RWDS transition (`:247-263`).
Neither has a `ready` input. **Read from source**; all `psram.py` line numbers
below are that file.

That is a promise to move a word per CK for as long as CS# is low. Every
candidate bus in §1–§3 lets the master decline a beat — Wishbone by not
asserting STB, B4 by not advancing under STALL, AXI4 by dropping `RREADY` or
`WVALID`, TileLink by dropping `ready`. **None of them removes the requirement
for an elasticity element.** Changing the bus changes when the master may
bubble, never whether it may.

There are exactly two structural answers, and they are the ends of §4 and §5:

1. put a buffer between the two so the rate mismatch is absorbed, or
2. make the device stall, so there is no mismatch.

Everything else is a variation on how fast the master bubbles.

## 1. Wishbone B4 pipelined — supported downstream, unreachable upstream

**`amaranth_soc` does support it.** `wishbone/bus.py:32` defines
`Feature.STALL = "stall"`; `Signature.__init__` adds the signal at `:127-128`;
`_FeatureShim` (`:256-322`) adapts pipelined↔classic in both directions.
`Arbiter` is already pipelined-correct — grant is re-evaluated only under
`with m.If(~self.bus.cyc)` (`:499`) and `ack` never enters the grant decision, so
a master may hold many transfers outstanding. **Read from source**, amaranth_soc
`0.1a1.dev32+g3e3d8b7`.

**`Decoder` is not.** `elaborate` (`:403-442`) selects `ack`, `dat_r`, `err` *and
`cyc`* inside `m.Switch(self.bus.adr)`. With several beats outstanding the
address on the bus is no longer the address that generated the arriving
response, so responses route by whichever window `adr` happens to be in, and
`cyc` to the previously addressed subordinate drops the moment `adr` moves — a
B4 violation. Safe only while a burst stays inside one window. **Read from
source.**

**VexiiRiscv will not emit it.** All four `toWishboneConfig()` set
`useSTALL = false`: `fetch/FetchCachelessBus.scala:51`, `fetch/FetchL1Bus.scala:65`,
`execute/lsu/LsuCachelessBus.scala:77`, `execute/lsu/LsuL1Bus.scala:439`.
SpinalHDL defines pipelined-ness as exactly that flag
(`Wishbone.scala:64-66`, `def isPipelined = useSTALL`), and nothing in
VexiiRiscv ever calls `.pipelined`. The bridges implement the classic handshake
in their own Scala — `LsuL1Bus.scala:367`,
`arbiter.buffered.ready := arbiter.buffered.valid && (bus.ACK || bus.ERR)` — so
flipping the flag would produce a port with a STALL wire and classic behaviour
behind it. **Read from source.**

A pipelined subordinate under a classic master buys nothing: there is never more
than one transfer outstanding. The master is what must change.

**COST.** Rewrite four VexiiRiscv Wishbone bridges in Scala to track outstanding
transfers, and rewrite `amaranth_soc`'s `Decoder` to route responses in issue
order rather than by live address. Then `stall` on 14 subordinates, or
`_FeatureShim` on each of them at a LUT cost per instance not determinable
without a build.

**RISK.** High and mostly upstream. The core is regenerable in seconds
([`riscv-core-build.md`](riscv-core-build.md)), so the Scala is tractable, but a
decoder that reorders responses is new logic on the exact path
`RegisteredResponse` was added to shorten.

**BUYS BACK.** Up to the full 6.2x, and only for masters that can actually run
ahead — and *still* needs §4's buffer, because a pipelined master may stall too.
Not worth it.

## 2. `RegisteredResponse`'s bubble is inherent, not incidental

**What it bought.** `d440f03`: `sync` fmax over four builds went 62.4–71.7 →
79.2–80.7 MHz against a 60 MHz constraint, and the spread collapsed 9.3 → 1.4
MHz. The path it cut was 16.45 ns of which 13.64 ns was routing, running
`arbiter.grant` → 3:1 address mux → decoder window compare fanned to eight
subordinates → ACK gather → arbiter mux → VexiiRiscv PMA check → register file
write enable. Cost +420 LUTs, +33 FF. **Measured.** It is load-bearing; nothing
below proposes removing it.

**Why the bubble cannot be buffered away.** The withhold is `sub.stb.eq(intr.stb
& ~answered)` (`wishbone_pipe.py:149`), and the docstring's stated reason —
preventing a second strobe on a side-effecting register — is real. But that is
not what makes the bubble unavoidable. This is:

On a classic bus the master supplies the address of every beat, including every
beat of a registered-feedback burst, and it may not present beat *N+1* until it
has seen the acknowledgement for beat *N*. Insert one register in the response
path and beat *N+1* arrives one cycle later. A skid buffer holds STB asserted; it
does not know what to put on `adr`.

For **reads** that is recoverable in principle: during `CTI=INCR_BURST` with
`BTE=LINEAR` the next address is `adr+1` and a shim could pre-issue it
speculatively, feeding the master from a two-entry FIFO. For **writes** it is
not: `dat_w` for beat *N+1* is not a function of anything the shim can see. A
speculative-address shim is therefore half a fix, and the half it is missing is
the half the board reported first.

Note also that a read shim which speculatively increments *is* §4's prefetch
buffer, arrived at from the other end and with a worse interface.

**What the bubble does on the wire**, since "the master is one cycle late" does
not obviously mean "the data is wrong". Nothing in the path can stall, so the
deficit appears as *extra words*:

    cycle 20   beat 0 low half
    cycle 21   beat 0 high half + ack
    cycle 22   beat 0 low half AGAIN   <- STB withheld; req_data falls back to a
                                          dat_w the CPU has not advanced
    cycle 23   beat 1 high half        <- now one word out of step
    cycle 24   beat 1 low half

The duplicate goes to a real address, and it leaves `BootRAM`'s `second_word`
(which free-runs with the device) one ahead of the window's (which resets per
beat), so every following beat goes out high-half-first. That is the
`8/16 correct, want 200f0e0d got 0e0d200f` signature: even beats right, odd beats
with their halves transposed.

**COST.** ~2-entry skid plus a speculative address counter and burst-abort
handling, reads only. Small, perhaps 100 LUTs. **Inferred** — not built.

**RISK.** Medium. Speculative addressing must not cross the window boundary or
issue past the master's `END_OF_BURST`, and it sits on the timing path that
`RegisteredResponse` exists to protect.

**BUYS BACK.** Reads only, and only if coalescing is re-enabled — which needs
§4 or §5 anyway for the write side. **Not independently useful.**

## 3. AXI4 and TileLink — one CPU flag, and no fabric to connect it to

**VexiiRiscv offers both.** `Param.scala:754-759` defines exactly six bus flags:
`--fetch-axi4`, `--fetch-wishbone`, `--lsu-axi4`, `--lsu-wishbone`,
`--lsu-l1-axi4`, `--lsu-l1-wishbone`. TileLink exists but has no CLI option — it
is reachable only through `soc/TilelinkVexiiRiscvFiber.scala:104-108`. BMB has
`toBmb()` methods with no callers. **Read from source.**

So `--lsu-l1-axi4` is one flag. The problem is the other side.

**`amaranth_soc` has no AXI4 and no TileLink.** A search of the whole installed
package returns zero matches; the only fabrics present are `wishbone` and `csr`.
**Read from source.** There is no decoder, no arbiter, no interconnect, and
nothing to attach the other 13 subordinates to.

That leaves two shapes, and both are bad:

- **AXI4 → Wishbone bridge.** Reintroduces the classic handshake at the bridge.
  Buys nothing at HyperRAM unless the bridge buffers a line — at which point you
  have built §4 and paid for an AXI4 port to get there.
- **HyperRAM on its own AXI4 port, bypassing the fabric.** Architecturally
  legitimate and the only version that helps. But HyperRAM is `exe=1`
  (`soc/top.py:689`), so `ibus` reaches it too and `--fetch-axi4` comes
  along, which means a two-master AXI4 arbiter written from scratch in Amaranth
  plus an AXI4 slave for the HyperRAM window.

**And it still needs the buffer.** An AXI4 master may deassert `RREADY` at any
beat. §0 applies unchanged.

**COST.** An AXI4 interconnect, arbiter and slave in Amaranth, none of which
exists. Several hundred to low thousands of LUTs; **not determinable from
source** without building one.

**RISK.** High, and the margin is the reason. The most recent utilisation figures
are on the `uart16550-console` branch at `bf819e3`, **not on `main`**: 12903 LUTs,
6942 FF, fmax 64.23 / 71.81 / 72.88 MHz min/median/max against a 60 MHz
constraint — under 7% at the bad end, and the review brief gives the current
state as 55% LUTs closing 69.5 MHz. A second full interconnect is the wrong thing
to spend that on.

**BUYS BACK.** Nothing that §4 or §5 does not, for far more work.

## 4. A line buffer — the answer if §5 turns out to be wrong

**`gateware/probes/hyperram/hyperram_fifo.py` is not this.** It is a throughput sweep
that alternates write and read chunks to find the granularity at which the
~23-cycle turnaround stops dominating — "somewhere between is the granularity a
FIFO should use, and this sweep finds it by measurement" (`:15-18`). It measures
what depth a *future* buffer should use. It is not a buffer. **Read from source.**

**Depth.** A 64-byte line is 32 × 16-bit device words = 16 × 32-bit beats.

- *Minimum for reads*, from the accumulated deficit: the master delivers a beat
  every 3 cycles and the device wants one every 2, so the drift is 1 cycle per
  beat, 16 words over a line. **16 words = 256 bits.**
- *Robust*: buffer the whole line, **32 words = 512 bits**, and the master's rate
  stops mattering at all.

**Writes have no minimum — the whole line must be resident before CS# drops**,
because the transaction cannot be opened until the last word is available. 32
words either way.

**Cost.** 512 bits fits in ECP5 distributed LUT RAM: 16 × 32 needs 8 `DPR16X4`
plus pointers and an FSM. **Inferred**, ~150–300 LUTs, no block RAM — which
matters, 42 of 56 are already used (`d440f03`). Read latency to the first beat is
unchanged (17 CK + 2 words either way); write latency gains one line-fill time
before the transaction starts.

**The prize is larger than 6.2x suggests, and it is shared with §5.** Today each
of the 16 beats pays `BootRAM`'s full IDLE → STARTING → BUSY → IDLE round trip,
which `7351eb9` identified as the dominant per-transaction cost. Either option
collapses 16 transactions into one and pays it once. **Inferred** — the 2–3x
measured is the floor for both, not the ceiling.

**What the buffer does *not* buy is end-to-end latency**, and the arithmetic is
worth doing before choosing on speed. Read: the device streams 49 CK while the
master drains at 3 cycles a beat, so the line lands at about `19 + 48 = 67`
cycles — the same as gating's ~66, because the master is the limit in both.
Write: the buffer must accumulate all 16 beats (48 cycles) **before** the
transaction opens, then spend 49 CK, so ~97 cycles against gating's ~66. **On
writes the buffer is slower.** Its real advantages are CS#-low time (49 CK
against 66, so more tCSM headroom) and freeing the controller for another
requester sooner. **Inferred from the cycle model above; not simulated.**

**RISK.** Medium, and concentrated in two places.

- *Burst length is not declared.* Registered-feedback signals `CTI=010` for "more
  coming" and `111` for "last", so a prefetcher must guess. The safe guess is the
  cache line, which is fixed at 64 bytes (`Param.scala:936`), gated on the
  address being line-aligned and `CTI=INCR_BURST`. Over-prefetch must be
  discardable and must not cross the window.
- *Writes become posted.* Acknowledging a beat into the buffer means `ERR` can no
  longer be reported for that beat. The window drives `bus.err.eq(0)`
  unconditionally today (`vexii_bootram.py:203`), so nothing regresses — but it
  forecloses ever reporting one.

**BUYS BACK.** The same 2–3x as §5, on both reads and writes, with no change to
the bus, the CPU, or `RegisteredResponse` — and it needs no datasheet clause to
be true, which is its one real argument over §5.

## 5. CK gating is legal, this part supports it by name, and the gate is already wired

**Cited, and this is the finding that reorders everything else.**

HyperBus Specification, §7.1.2 *Active Clock Stop*:

> Active Clock Stop is a read / write operation where the clock has not
> transitioned for an extended period, without CS# going High and the device
> internal logic has gone into Standby Mode to conserve power. Slave read output
> data or master command, address, or write output data is latched and remains
> actively driven and valid during Active Clock Stop during transaction periods
> where the master or slave would normally drive their output.

and §3, *HyperBus Protocol*:

> The clock is not required to be free-running. The clock may be idle while CS#
> is High **or may stop in the idle state while CS# is Low (this is called Active
> Clock Stop)**. Support for Active Clock Stop is slave device dependent. It is
> an optional HyperBus device feature.

That last sentence is why the spec alone does not settle it, and why the part's
own datasheet below is the citation that matters.

**The W956A8 is one of the devices that supports it.** Winbond
`W956D8MBYA / W956A8MBYA`, revision A01-006, 2022-07-29, §10.2.2 *Active Clock
Stop*:

> The Active Clock Stop state reduces device interface energy consumption to the
> I_CC6 level during the data transfer portion of a read or writes operation. The
> device automatically enables this state when clock remains stable for tACC +
> 30 nS. While in Active Clock Stop state, read data is latched and always driven
> onto the data bus. Active Clock Stop state helps reduce current consumption
> when the host system clock has stopped to pause the data transfer. Even though
> CS# may be Low throughout these extended data transfer cycles [...] **Active
> read or write current will resume once the data transfer is restarted with a
> toggling clock. The Active Clock Stop state must not be used in violation of
> the tCSM limit. CS# must go High before tCSM is violated. Note that it is
> recommended to stop the clock when it is in Low state.**

It is a named device state with its own truth-table row (§10.1), its own DC
parameter (I_CC6, 5 mA typical / 8 mA maximum) and its own timing figure — Figure
13, *Active Clock Stop during Read Transaction (DDR)*, which draws CS# held low
across a flat CK annotated "Clock Stopped" between two words of a read data
phase. **That figure is this question, drawn.** Not an inference from silence.

**The manifest will not reproduce these quotes.** `sources/**/*.pdf` is
gitignored and `sources/README.md` carries the URLs — but its Winbond entry is
`rev A01-002 (Nov 2019), 10 pp`, an abridged datasheet, and §10.2.2 is on page 27
of the full one. Anyone re-checking the citation against the file the manifest
points at will not find it. The manifest needs a full-length revision added;
revisions and dates are given above so the quotes stay re-findable meanwhile.

**This design does not even need the clause.** The bubble it has to cover is one
`sync` cycle per beat, which stretches the worst-case CK period to 33.3 ns at
`SYNC_MHZ = 60`. That is inside `tCK` max on both datasheet revisions — and the
two revisions differ, which is worth knowing before writing a long stall:

| revision | Table 22 `tCK` max |
|---|---|
| A01-003, 2020-07-24 | `–` (unbounded) |
| A01-006, 2022-07-29 | **100 ns** |

Note 2, present in both, resolves it in favour of §10.2.2: *"Minimum Frequency
(Maximum tCK) is dependent upon maximum CS# Low time (tCSM), Initial Latency and
Burst Length."* The binding constraint is tCSM, not a clock floor. **But a stall
longer than 100 ns — six `sync` cycles at 60 MHz — sits in the gap between the
two clauses on A01-006 silicon, and this design should not go there.** Bound the
stall and close the transaction if the master has not returned; a 16-beat line
gated at 3 cycles per beat holds CS# low for ~66 `sync` cycles = **1.1 µs against
a 4 µs tCSM**, 3.6x margin, with no individual gap over 33.3 ns.

**tCSM is 4 µs because CR1[1:0] = `01b`**, which Table 12 ties to a 64 ms array
refresh interval over 8192 rows at T_CASE < 85 °C, halved. Stall time counts
against the same budget as clock time. **Cited.**

**The gate is already in the gateware.** `HyperRAMPHY` drives CK from
`ODDRX1F(i_D0=self.phy.clk_en, i_D1=0)` (`psram.py:328-334`): one CK pulse per
`sync` cycle when `clk_en` is high, **and CK held Low when it is not** — which is
the state the datasheet recommends stopping in. The FSM already uses it, holding
`clk_en` low in `IDLE`, `LATCH_RWDS` and `RECOVERY`. **Read from source.**

**The read path stalls for free.** `READ_DATA` gates `read_ready` on an RWDS
*transition* — `self.phy.rwds.i == 0b10`, or the inverted-clock case
(`psram.py:247-263`). RWDS is the device's read strobe and stops transitioning
when CK stops, so `read_ready` never asserts during the pause. **Read from
source**, and consistent with §7.1.2's "read output data is latched and remains
actively driven".

Note what the datasheet does *not* promise: §10.1's interface-state table gives
RWDS as **`X` — "either VIL, VIH, VOL or VOH"** in the Active Clock Stop row. The
*level* is undefined. Nothing may infer anything from it; only the absence of a
transition is guaranteed, and that is all luna's FSM uses. **Cited.**

**The write path needs one gate, and it is not in luna.** `write_ready` is
combinationally 1 in `WRITE_DATA`. But nothing forces the consumer to believe it:
`vexii_bootram.py:606` is the single point where the controller's readiness
becomes this SoC's,

    word_event = Mux(writing, psram.write_ready, psram.read_ready)

and everything downstream — `second_word` (`:631`), `mmap.in_valid` (`:717`),
`live_data` via `mmap.req_data` (`:637-639`) — advances only on `word_event`.
Gate it with `~stall` and the controller re-latches the same word into `dq.o`
every cycle while CK is stopped, which is exactly what the device expects to see
held. **Read from source.**

So the change is: one `HyperBusPHY` record split in two with
`clk_en_dev = clk_en_ctrl & ~stall`, and `& ~stall` on one existing `Mux`.
**No modification to `HyperRAMInterface`**, which
[`upstream-boundary.md`](upstream-boundary.md) records under "Decided: HyperRAM
splits at the PHY" as **upstream, unchanged** — and which this leaves that way.
The gating wrapper is workspace gateware of the same kind as
`HyperRAMWishbone`.

**COST.** ~10 lines of pass-through wiring plus a stall term. **Inferred**, tens
of LUTs. No RAM, no new FSM, no bus change.

**RISK.** Low, with five named edges — four of them from the datasheet:

- **Never pause inside the latency window.** The HyperBus spec §7.1.2 says the
  data lines are High-Z during latency/turnaround, so `HANDLE_LATENCY` must run
  ungated. Deriving the stall from `~mmap.req` satisfies this by construction —
  see the experiment below — but a stall term derived from anything else must
  be qualified explicitly. **Cited.**
- **Only on word boundaries**, which `word_event` already gives for free.
- **Park CK low** — §10.2.2's recommendation, and §10.1 defines the Idle state as
  CK Low. `ODDRX1F(D0=clk_en, D1=0)` does exactly that. **Cited.**
- **Bound the stall at 100 ns** on A01-006 silicon, per the table above, and
  close the transaction rather than exceed it.
- The `sync` cycle in which the stall asserts must not be one in which the
  controller's registered `dq.o` has already moved on. Sim settles it.

Also: `HYPERRAM_MAX_BURST_WORDS` must become a *time* budget rather than a word
count — see the defect below — because stalling decouples CS#-low time from word
count. And `tACC + 30 ns` is when the device drops to I_CC6; resuming from there
is specified to work but has never been exercised on this board.

**Someone has already built this.** `fpga-professional-association/hyperram`
(public, SystemVerilog) carries an `ACTIVE_CLK_STOP` parameter doing precisely
this — `phy_ck_en = ~rd_backpressure`, pausing CK on word boundaries when its
read FIFO passes a high-water mark — with a Verilator testbench (`tb_clkstop.sv`)
asserting that the gated build returns all words correct where the ungated one
corrupts. Simulation-proven there, not silicon-proven. **Third-party, read from
source, not verified here.** LiteX's `hyperbus.py` is the contrast: it chops the
transaction and raises CS# rather than stalling, so it is evidence of neither.

**BUYS BACK.** The full 2–3x with coalescing re-enabled — 16 transactions per line
collapse to one, and with them 15 of 16 `BootRAM` round trips — at the smallest
change of any option here.

### On silicon it gives 1/16, and the read gate is not why

**Both flags are still off, and now for a measured reason.** Active Clock Stop
passes 16/16 in simulation and gives **1/16 on the board**.

A read-delay sweep across all four settings showed the stall firing
(1,632–6,222 cycles) and the selector working monotonically, with
**byte-identical data at every setting** — so read-gate alignment is not the
cause, and the difference is not where the model said it would be. **Unexplained**
(#185).

The modelling gap that motivated caution is real and separate: the model serves
read data in the same cycle as the CK that asked for it, and silicon does not.

## Ranked

1. **CK gating (§5).** Legal, cited, device-supported, gate already present, two
   small edits, no buffer, no bus change. Done, and simulation-proven; the
   remaining work is a board run and the read round-trip measurement above.
2. **Line buffer (§4).** Do this if §5 fails on the board. Not "and then this for
   more speed" — it is not faster end-to-end, and on writes it is slower. What it
   buys is CS#-low time (49 CK against ~66) and therefore tCSM headroom, plus a
   controller free sooner for the CSR and JTAG requesters. Independent of §5, and
   they compose if both are wanted.
3. **Speculative-address read shim (§2).** Only half a fix and it converges on §4
   anyway. Skip.
4. **Wishbone B4 (§1).** Upstream Scala plus an amaranth_soc decoder rewrite, and
   §0 still applies. No.
5. **AXI4 / TileLink (§3).** A whole interconnect that does not exist, to reach
   the same place. No.

`sustained=False` should stay the default until §5 or §4 is on the board. It is
correct, and correctness at 5.43 MB/s beats corruption at 20.70.

## The smallest experiment, run — and it passes

**Measured**, `scripts/soc_hyperram_sim.py` §11, 2026-08-05. Coalescing on, CK
gated, the SoC's real bus path including `RegisteredResponse`:

| | write | read |
|---|---|---|
| beats correct | **16/16** | **16/16** |
| device words for a 64-byte line | **32** | — |
| HyperBus transactions | **1** | **1** |
| CK inside CS# | 48 | 48 |
| cycles with CK withheld | 15 | 16 |

against §9's negative control, which is kept and still asserts the board's
`8/16 correct, bad 1010101010101010`, `want 200f0e0d got 0e0d200f`, 48 words.
**The 48 CK is the same figure an ungated coalesced burst costs**, because the
model counts CK and not `sync` cycles: the stall spends CS#-low time and no
clock. The read is one CK *below* §8's ungated 49 — upstream leaves `clk_en`
high for the first `RECOVERY` cycle, which a write needs and a read does not, and
the gate suppresses the word the ungated engine clocks out of the device and
throws away.

Three things changed against the sketch above, and the third is the one that
mattered.

1. **`ModelHyperRAM16`'s RWDS and its address advance are gated on `clk_en`**,
   and so is its latency countdown. The last of those is what makes the harness
   able to *fail*: the device counts latency in CK and `HANDLE_LATENCY` counts
   `sync` cycles, so a pause inside the window would shift the whole data phase
   and every beat would be wrong.
2. **The record split is `ClockStopPHY` in `gateware/soc/bootram.py`**,
   not a harness wrapper — the simulation drives the same gateware a build would.
   `BootRAM` gains `clock_stop`, default **off**.
3. **`clk_en_dev = clk_en & ~stall` is one register short on writes.** `dq.o` is
   registered inside `HyperRAMInterface`, so the word on the wire in cycle *T* is
   the one `write_ready` accepted in *T−1*; gating *T* with the same term that
   gates `word_event` discards it. A read's data and its RWDS transition arrive
   in the cycle `read_ready` fires, so reads want no register. The gate is
   `Mux(writing, stall_q, stall)`. §5's fifth named risk was exactly this, and
   the arrangement without the register is worse than no gate at all — 0/16 and
   31 addresses touched, which §11 now asserts as a second negative control.

**The latency-window claim is measured, not argued.** §11 records the device's
own state on every cycle the gate withholds a clock and asserts they are all in
the data phase. They are: 31 stalled cycles, all `data`, one per Wishbone beat.
The structural reason stands — `req` is `pending | (bursting & cyc & stb)`,
`pending` clears only on a `word_event`, and a `word_event` exists only in
`WRITE_DATA`/`READ_DATA`, so `req` is high across `SHIFT_COMMAND` and
`HANDLE_LATENCY` whatever the master does.

**What the simulation cannot settle, and it is the read side.** This model serves
read data in the same cycle the CK that asked for it, a round trip of zero. Real
silicon has one: `HyperRAMPHY` samples DQ and RWDS through `IDDRX1F`, and the
part's own tACC comes before that. Stopping CK at cycle *T* on hardware stops
data arriving at *T+N*, and for *N* cycles after the stall the controller will
keep asserting `read_ready` at a window that is no longer listening. If *N* = 1
the read side wants the *registered* gate — the same one the write side needs —
and the alignment this file measures as correct is correct only for *N* = 0.
**Writes are unaffected: they are entirely master-timed and the model's write
path is the hardware's.** So the write half is ready for a board; the read half
needs `N` measured first, and the cheap way to measure it is a gated read of a
known pattern with the gate's alignment as the variable.

Then a board run of `hr cross` and `bench`.

## Why none of the tests caught this

Every path that could have found the corrupt burst was blind to it, which is the
part worth keeping:

* `bench hyperram` only **reads**.
* `hr test` / `hr read` move one 16-bit word at a time through the staging CSR,
  which never opens a multi-word transaction.
* `soc_hyperram_sim.py` covered burst **reads** and **single** writes — there was
  no burst-write case at all.
* A cross-port check writes and reads through the same path, so read and write
  skew largely cancel, which is why a *total* read fault presented as a *half*
  write fault.
* Since `.text` moved to flash, firmware never writes a cache line to HyperRAM in
  normal operation.

The board had already measured it and nobody recognised it: `7351eb9` recorded
`words 10848 against 3616 beats, exactly 3.0 per 32-bit beat`, flagged as not
understood. Three words per beat is this fault, in the units it happens in.

**`RegisteredResponse` must be in the simulated path.** A harness that drives
`mmap.bus` directly models a master that replaces a beat on the acknowledging
edge — which this SoC is not — and reports **16/16 correct** with the bug
invisible. That single insertion is the difference between a model that agrees
with the board bit-for-bit and one that contradicts it.

## Two defects found on the way, neither caused by any of the above — both fixed

**`HYPERRAM_MAX_BURST_WORDS` was 3.1x too permissive at the clock this SoC runs.**
`bootram.py` set 748 words with the comment "below 768 CK at 192 MHz" —
4 µs, correct for CK 192. `SYNC_MHZ` is 60 and `HYPERRAM_DQS` is False, so CK is
60 MHz and 748 words was **12.5 µs, over three times tCSM**. Unreachable because
a Wishbone burst never exceeds 32 words, and dead entirely while
`sustained=False` — but it was a word count guarding a time limit, and §5 makes
CS#-low time stop tracking word count at all.

Now `hyperram_max_burst_words(ck_mhz, clock_stop=)`, from tCSM with a tenth of
the budget held back for the PLL's actual solved frequency and for `RECOVERY`
being a TODO upstream. `top.py` passes its own `SYNC_MHZ` in rather
than a second copy of the number being kept here, because
`riscv_clock_ladder.py` rewrites that constant. **198 words at CK 60, 132 with
clock stop, 674 at CK 192** — and §8 checks all four against tCSM as well as
checking that the literal 748 would have failed at this clock, so the derivation
is discriminating.

**`ModelHyperRAM16` drove RWDS ungated.** `rwds_i = 0b10` in the data state
regardless of `clk_en`, so the model asserted a read strobe on a clock edge that
never happened. It had not mattered because `clk_en` was high throughout every
data phase simulated. Now gated, along with the read address advance and the
latency countdown — see the experiment above, where the last of those is what
lets the harness catch a pause in the wrong place.

**`ModelHyperRAM16` entered its data phase on the wrong cycle.** It counted
`HIGH_LATENCY_CLOCKS` where the controller loads `HIGH_LATENCY_CLOCKS - 2`, and
recorded **zero** words for a single write. Reads never exposed it: RWDS gates the
controller's sampling so the model simply waited, but a write is not strobed, so
the words went past while the model was still counting.

## What could not be determined

- Whether the installed `amaranth_soc` carries local patches. It was pip-installed
  from `tmp/forks/amaranth-soc`, which no longer exists; the version string says
  32 commits past `v0.1a1` at `3e3d8b7`. **Not determinable from source.**
- The LUT cost of any option here without a build. Every area figure above marked
  *inferred* is an estimate and is labelled as one.
- Whether resuming from Active Clock Stop works on this board. The datasheet
  specifies it; simulation now assumes it; nothing has exercised it on silicon.
- **The read path's round trip**, N cycles from a CK to the data it fetches
  arriving at `phy.dq.i`. The model's N is zero and the gate's read alignment is
  correct only for that. **Not determinable from source** — it is a measurement,
  and it is the one thing between the write half of this and a board run.
