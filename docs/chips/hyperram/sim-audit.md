# `soc_hyperram_sim.py`, check by check

Every assertion in
[`../../../scripts/soc_hyperram_sim.py`](../../../scripts/soc_hyperram_sim.py)
classified, so
[#346](https://github.com/awtoau/cynthion-workspace/issues/346) can be executed
without guessing what a deletion costs.

Baseline: **143 assertions, all passing**, at `ba64439`. Classified from the
assertion expressions, not from the section titles — three checks say one thing
in their label and test another, and that is the fault
[#346](https://github.com/awtoau/cynthion-workspace/issues/346) was opened over.

## Classes

| class | meaning | where it can live |
|---|---|---|
| **C** conformance | asserts a fact about the PART | must be independent of the controller — twin or vendor model |
| **F** fault injection | device or caller misbehaves; the CONTROLLER must survive | device-side → twin knob; caller-side → the driving testbench |
| **I** integration | the SoC ABOVE the controller — window, arbiter, refill, clock stop | stays in Amaranth; 25 of 54 need no device at all |
| **X** controller | the controller alone, under a FAITHFUL device | stays, wherever a faithful device is available |
| **S** structural | a grep or `hasattr` over source text; no simulation | stays; belongs in a source lint |
| **0** tautological | cannot fail for the reason its label gives | delete or repair |

**`X` and `S` are findings, not decoration.** #346's three-way split has no home
for 33 of the 143 assertions. They are neither part-facts nor
misbehaviour nor SoC-layer: they are the controller's own arithmetic and the
vendored-vs-upstream source diff. Retiring the Python model on the strength of a
conformance/fault/integration split alone would strand them.

## Counts

| class | checks | share |
|---|---|---|
| **I** integration | 54 | 38% |
| **F** fault injection | 29 | 20% |
| **C** conformance | 25 | 17% |
| **X** controller | 24 | 17% |
| **S** structural | 9 | 6% |
| **0** tautological | 2 | 1% |

### The 25 conformance checks, by what they rest on

| rests on | checks | verdict |
|---|---|---|
| the twin + vendor pair already establishes it | **12** | re-point via the bridge, then delete here |
| the CONTROLLER's own latency constant | **3** | **circular — cannot fail for a part reason.** Delete |
| a datasheet number, arithmetic only, no model | **6** | keep — nothing to be circular with |
| tCSHI, detected by the Python model, twin silent | **4** | **the twin has no tCSHI check.** Port the check, not the model |

### The 29 fault-injection checks, by whose fault it is

| side | checks | knob | moves to |
|---|---|---|---|
| device never answers (`deliver=0`) | 8 | `deliver` | twin |
| device stops after N beats (`deliver=7`/`8`) | 7 | `deliver` | twin |
| device's RWDS over the CA (`rwds_stale`/`rwds_extra`) | 2 | RWDS level | twin |
| **caller** drops `final_word` / `perform_write`, or never ends the burst | **12** | `run`/`run16` arguments | **the driving testbench, NOT the model** |

**Twelve of the twenty-nine are caller-side.** They inject nothing into the
device; they misdrive the controller. No knob on `hyperram_model.v` can carry
them — they need a testbench that can be told to misbehave, which is
[`controller_model_tb.sv`](../../../gateware/probes/hyperram/controller_model_tb.sv)'s
burst engine or a cocotb driver. Listed in #346's plan as if they were model
capabilities; they are not.

---

## 1. `section_command` — the command the device decodes

| check | class | note |
|---|---|---|
| the command read: every transaction returned to IDLE | X | liveness, faithful device |
| one command was issued | X | one CS# assertion per request |
| the device decodes the address that was asked for | C | twin: **yes** (bridge case 2, top-address) |
| it decodes as a read | C | CA[47]; twin: **yes** |
| it decodes as memory, not register space | C | CA[46]; twin: **yes** |
| it decodes as a linear burst | C | CA[45]; twin: **yes** |
| the command matches the specification's encoding | C | full 48-bit CA against `encode_ca`; twin: **yes** |
| reading the same capture 16 bits wide gives a WRONG address | **0** | see below |

**The 16-bit check cannot fail.** It takes every other byte of the captured CA,
rebuilds an address and asserts it differs from `TEST_ADDRESS` — a property of
the constant, with neither controller nor device in the loop. Documentation of a
trap, written as an assertion.

Circularity: `encode_ca` and `ModelHyperRAM._decode` are both written in this
file from one reading of the spec. The five C rows pass if that reading and the
controller are wrong together — which is exactly #346's complaint, and exactly
what the vendor model settles.

## 2. `section_latency` — fixed latency

| check | class | note |
|---|---|---|
| the controller's two latency counts differ | X | luna class constants |
| the fixed-latency read: every transaction returned to IDLE | X | |
| a read against the fixed-latency model returns data | X | **label says latency, assertion is `read_beats > 0`** |
| the read raises BURSTDET, so DQS is what found the data | **0** | **same predicate as the row above.** `burstdet` is never read |
| the SHORT count would sample inside the latency window | **C, circular** | `window` IS the controller's own long count — see below |
| upstream forces the long branch unconditionally | S | grep for `extra_latency \| 1` |

**Section 5b's pathology, still present here.** The model's latency is
`latency_beats()`, the controller's own count; the file says so in its own
comment. So "the short count falls inside the window" compares
`LOW_LATENCY_CLOCKS + 1` against `HIGH_LATENCY_CLOCKS + 1` — two constants from
one class. It cannot fail unless luna edits its constants, and it claims a fact
about the part.

The twin answers the real question and the bridge already asks it:
`controller_model_tb.sv` sweeps `CR0[7:4]` in fixed and in variable latency and
checks the controller's first data beat against the model's own decode.

## 3. `section_held` — held, not pulsed

| check | class | note |
|---|---|---|
| held: the word arrives at the address asked for | X | good arrangement |
| pulsed `perform_write`/`write_data`: the device does NOT get it | **F, caller** | `hold_write=False` |
| pulsed `final_word`: the burst does not end where it was meant to | **F, caller** | `hold_final_word=False` |
| pulsed `final_word`: every transaction returned to IDLE | **F, caller** | liveness under a caller that dropped the only signal that could end it |

Three caller-side faults. The device is faithful throughout.

## 4. `section_recovery` — tCSHI, DQS

| check | class | note |
|---|---|---|
| upstream's RECOVERY state is still a TODO | S | grep |
| upstream's controller VIOLATES tCSHI back-to-back | C | negative control; **twin: no tCSHI check** |
| back-to-back DQS reads: every transaction returned to IDLE | X | |
| the vendored controller keeps tCSHI with NO gap from the master | C | **twin: no tCSHI check** |

`T_CSHI_NS = 10.0` is stated in this file from the datasheet and confirmed by
nothing. `hyperram_model.v` checks tCSM and not tCSHI, so re-pointing these
means **adding a tCSHI check to the twin**, where the vendor model can contradict
it.

## 4b. `section_recovery_non_dqs` — tCSHI, the shipped path

| check | class | note |
|---|---|---|
| upstream's non-DQS controller VIOLATES tCSHI back-to-back | C | twin: no |
| ...and it is the GAP that differs, not the transaction count | X | |
| back-to-back non-DQS reads: every transaction returned to IDLE | X | |
| the vendored controller keeps tCSHI with NO gap from the master | C | twin: no |
| ...and still issues both transactions | X | |
| ...at the addresses asked for | C | CA decode; twin: **yes** |
| sixteen classic transactions through the window keep it too | I | window in the path |

## 9b. `section_as_built` — the configuration that synthesises

| check | class | note |
|---|---|---|
| as built: every transaction returned to IDLE | X | |
| as built: a read issues a command the device decodes | X | |

The interesting result here — whether the as-built write lands — is **emitted,
not asserted**, because the model's device latency has never been reconciled with
the part. That reconciliation is what the twin is for.

## 7b. `section_dqs_write_order` — **zero assertions**

The section runs, prints, and checks nothing: `jtag_ack` never arrives, so
nothing reaches the model. Its question — which 16-bit half of a DQS write hits
the wire first — is a conformance question the twin and vendor already answer at
the device end; the controller end needs the bridge.

**Counts as coverage in the section list and is not.**

## 5b. `section_latency_input` — `latency_clocks` is live (#331, #338)

| check | class | note |
|---|---|---|
| an undriven `latency_clocks` still reaches the data body | X | FSM occupancy, no device data |
| undriven matches the build-time constant exactly | X | |
| each latency setting waits a different number of cycles | X | the corrected 5b: counts `HANDLE_LATENCY`, not `read_ready` |
| the wait tracks the setting one for one | X | |
| a count below 2 is clamped rather than wrapped | X | |
| `fixed_latency` is an input, not a compile-time constant | S | `hasattr` |
| driving `fixed_latency` low reaches the variable branch | X | **rests on a model behaviour the twin contradicts** |

The last row works because `ModelHyperRAM16` holds RWDS **low** through the CA by
default. The twin now drives RWDS from `CR0[3] | take_long` after a 12 ns tDSV
float, so under fixed latency the real part holds it **high**. The Python model
has no CR0 at all and cannot follow — the check is still sound as a controller
check, but its device is not the part.

## 5. `section_structural` — 6 checks, all S

Six greps over `hyperram_dqs_phy.py` and luna's `psram.py`. No simulation, no
model, nothing to re-point. They belong in a source lint and are unaffected by
any of this.

## 6. `section_wishbone` — 13 checks, all I

`HyperRAMWishbone` alone. **No device model in the harness at all.** Delayed
grant, address doubling, little-endian pairing, partial-store read-merge-write,
the tCSM-safe burst cap. Untouched by #346.

## 7. `section_shared_engine` — 12 checks, all I

`BootRAM` against `ControlledInterface`, a bare signal surface. **No device model
at all.** Untouched by #346.

## 8. `section_line_refill` — CTI coalescing

| check | class | note |
|---|---|---|
| a 16-beat incrementing burst issues ONE HyperBus transaction | I | |
| the pre-change classic arrangement issues SIXTEEN transactions | I | negative control |
| the coalesced refill returns all sixteen Wishbone beats | I | |
| the classic negative control returns the same sixteen beats | I | |
| one line occupies 49 CK with command and fixed latency | **C, circular** | see below |
| sixteen classic transfers occupy 304 CK | **C, circular** | see below |
| the cap at CK 60 fits in tCSM | C | arithmetic on `T_CSM`, no model |
| the cap at CK 60 with clock stop fits in tCSM | C | |
| the cap at CK 192 fits in tCSM | C | |
| the cap at CK 192 with clock stop fits in tCSM | C | |
| the old fixed 748-word cap would NOT have fitted at this CK | C | negative control, arithmetic |
| a 64-byte line is well inside the cap | I | |

**49 and 304 CK are the controller's own latency, measured back.**
`ModelHyperRAM16._latency = HyperRAMController.HIGH_LATENCY_CLOCKS - 2` — the
file's own comment says "count from the same value the controller loads, so the
two agree by construction". Change the controller's constant and both numbers
move together; the check reports agreement it built in. The five cap rows are
different: they are arithmetic against a datasheet number with no model in the
loop, so there is nothing for them to be circular with.

## 9. `section_line_write` — a line through the SoC's real bus path

| check | class | note |
|---|---|---|
| a single 32-bit write stores exactly two device words | I | |
| ...at the doubled address, low half first | C | word order; twin: **yes** |
| coalescing across a bubbling master reproduces the BOARD | I | 8/16, `1010101010101010` |
| ...with the board's first bad beat, halves transposed | I | |
| ...and 48 device words written for a 32-word line | I | |
| the shipping window writes all sixteen beats correctly | I | |
| a 64-byte line touches 32 device words and not one more | I | |
| every word lands at the address the beat asked for | I | |
| one HyperBus transaction per beat, since none may be held open | I | |

The model is a **bookkeeping device** here — an address-indexed dict that records
what landed. It is not answering a protocol question, and the twin would serve
the same role if the harness could reach it.

## 10. `section_line_read_bubble` — 2 checks, both I

The same fault on reads, against pre-filled memory.

## 11. `section_clock_stop` — Active Clock Stop

| check | class | note |
|---|---|---|
| a coalesced line write is correct once CK can stop | I | |
| ...in 32 device words, not the ungated 48 | I | |
| ...at the address each beat asked for | I | |
| the line is ONE transaction per direction, not sixteen | I | |
| the gated line costs no extra CK, and a read saves one | I | `[48, 48]`; **the CK total carries the controller's latency constant** |
| a coalesced line read returns all sixteen beats | I | |
| ...in one transaction | I | |
| every withheld clock fell inside the device's data phase | I | **"data phase" boundary is the controller's own count** |
| one withheld clock per beat, 15 writing and 16 reading | I | |
| the same run without the gate still reproduces the board | I | negative control |
| gating CK level with the word stall corrupts EVERY beat | I | wrong arrangement, worse than no gate |
| ...and does not even touch 32 device words | I | |
| the stall bound is inside rev A01-006's 100 ns tCK maximum | C | arithmetic; twin does not model a tCK maximum |
| ...and the bubble it has to cover is one cycle, well inside it | I | |

Two rows carry a circular component without being conformance claims: the `48`
CK totals and the "data phase" boundary both come from
`HIGH_LATENCY_CLOCKS - 2`. The *claims* are about the gate, so they survive; the
*numbers* would change with the controller.

## 12. `section_escape` — every transaction ENDS (#316)

23 checks. **The most valuable section in the file, and the one #346 is about.**

| group | checks | class | knob |
|---|---|---|---|
| upstream never returns from a silent device (non-DQS, DQS) | 2 | F device | `deliver=0` |
| upstream never returns from 7-of-8 | 1 | F device | `deliver=7` |
| upstream's WRITE_DATA never returns from a stalled consumer (non-DQS, DQS) | 2 | F caller | `beats=0` |
| ours: silent device ends, and `timed_out` says the watchdog did it | 4 | F device | `deliver=0` |
| ours: 7 of 8 ends, took 7 beats, flagged | 3 | F device | `deliver=7` |
| ours: 8 of 8 ends unflagged, and sooner than the watchdog | 3 | F device | `deliver=8`, the control |
| ours: stalled consumer ends, flagged (non-DQS, DQS) | 3 | F caller | `beats=0` |
| register read against a silent device ends | 1 | F device | `deliver=0` |
| register write ends on its own, unflagged (non-DQS, DQS) | 4 | X | faithful device |

Device-side 14, caller-side 5, controller 4.

The negative controls are load-bearing: upstream's controllers are run through
the same harness and **required to hang**. Any replacement must keep them, or the
positive checks prove nothing — the same rule
[`bist-plan.md`](bist-plan.md) states.

## 13. `section_tcsm` — CS# Low never exceeds tCSM (#317)

| check | class | note |
|---|---|---|
| upstream holds CS# Low past tCSM on a burst nobody ends | F caller | negative control; part fact tCSM, twin: **yes** |
| ours chops it, and CS# never passes tCSM | F caller | |
| ...at the word cap exactly, not one beat past it | X | |
| ...and the caller is told the controller ended it | F caller | |
| a silent device does not hold CS# past tCSM either | F device | `deliver=0` |
| the DQS controller keeps tCSM on the same shape | F caller | |

tCSM is the one part parameter in this file the twin *does* check — it prints an
`ERROR tCSM violation` past 4 us, matched to the vendor model.

## 14. `section_ca_rwds` — the extra-latency sample (#321, #338)

| check | class | note |
|---|---|---|
| RWDS raised mid-CA takes the LONG latency | F device | `rwds_extra=1` |
| a level left over from before the CA does NOT extend it | F device | `rwds_stale=1` — **the #338 case** |
| ...and the two choices are distinguishable at all | X | constants differ |

**This is the section #338 needs and the model is weakest at.** The Python model
splits stale from answered at `_cs_low_cycles > 1` — a hand-chosen tDSV of one
cycle, driven as a hard 0 or 1, never floating. The twin now models the real
thing: RWDS **tri-stated for `T_DSV_NS = 12 ns`** after CS# falls, then driven
from `CR0[3] | take_long`. A controller that samples inside that window reads
`z`, and the Python model cannot express `z` at all.

`ModelHyperRAM` — the DQS model — **has no RWDS path whatsoever**, so the DQS
controller's extra-latency sampling is checked nowhere.

## 15. `section_register_ca` — CA[45] for register space (#320)

Five checks, **all C**, and all covered by the twin:

| check | twin |
|---|---|
| upstream emits CA[45]=0 for a single_page register write | negative control |
| ours forces CA[45]=1 there | yes |
| ...which is the datasheet's 0x60 for a register write | yes — `CMD_REG_WRITE = 8'h60` |
| a WRAPPED memory burst is left alone -- CA[45]=0 | yes — `CMD_MEM_READ_WRAP = 8'h80` |
| the DQS controller forces it too | yes |

---

## What the twin already covers

From
[`vendor_model_tb.sv`](../../../gateware/probes/hyperram/vendor_model_tb.sv)
(the device alone, held equal to Winbond's model) and
[`controller_model_tb.sv`](../../../gateware/probes/hyperram/controller_model_tb.sv)
(our controller in front of it):

| conformance question | checks here | twin |
|---|---|---|
| CA field positions, address / R‑W / space / burst type | 6 | **yes** — bridge cases 1 and 2, plus the top-address truncation case |
| CA[45] forced for register space, `0x60` / `0x80` command bytes | 5 | **yes** |
| word order — low half at the lower address | 1 | **yes** — bridge case 2 burst write and read-back |
| latency count against `CR0[7:4]`, fixed and variable | 1 (circular) | **yes** — bridge cases 3, 4 and 5 sweep it |
| tCSM | via §13 | **yes** — `ERROR tCSM violation` at 4 us |
| RWDS over the CA, tDSV, the extra-latency request | 2 | **yes, and better** — real tri-state |
| refresh-forced 2x election under variable latency | none | **yes**, both models since `916ca3f` |
| **tCSHI** | 4 | **NO** |
| **tCK maximum (stall bound)** | 1 | **NO** |

## Knobs on the twin

Only the device-side faults, and there are three, not four. All three landed on
[`hyperram_model.v`](../../../gateware/probes/hyperram/hyperram_model.v); each
defaults to the part, so the shared testbench is unchanged.

| parameter | default | fault | checks it carries |
|---|---|---|---|
| `DELIVER_WORDS` | `-1` | `0` never answers, `N` stops after N words | 15 |
| `CA_RWDS_FAULT` | `0` | `1` stuck High, `2` stuck Low, `3` never driven | 2 |
| `REFUSE_REG_WRITE` | `0` | `1` takes the write and drops it | **0 — a new capability, not a port** |

`DELIVER_WORDS` is one knob, not two: "never answer" is `0`, which is how
`soc_hyperram_sim.py` already writes it.

Proved by [`fault_model_tb.sv`](../../../gateware/probes/hyperram/fault_model_tb.sv),
Icarus-only — the vendor model has no such parameters, so what holds it honest is
that the same model still passes `vendor_model_tb.sv` with every knob at 0:

    scripts/hyperram_vendor_model_sim.py --sim fault

| run | bus |
|---|---|
| control | `ca rwds = zz1111`, strobe at 28 edges, `id0 = 0c86`, 8 of 8 words, `cr0 = af2f` |
| `CA_RWDS_FAULT=1/2/3` | `111111` / `000000` / `zzzzzz` |
| `DELIVER_WORDS=0` | `id0 = zzzz`, 0 of 8 words |
| `DELIVER_WORDS=3` | 3 of 8 words |
| `REFUSE_REG_WRITE=1` | `cr0 after write = 8f2f` |

A held fault has no tDSV — that is what makes `111111` distinguishable from the
part's `zz1111`, and it is the float #338 suspects.

The remaining 12 fault-injection checks are **caller-side** and no model knob can
carry them.

## What cannot be deleted, and why

| group | checks | reason |
|---|---|---|
| integration with no device model at all (§6, §7) | 25 | nothing to re-point; #346 does not reach them |
| integration through `ModelHyperRAM16` as a bookkeeping array (§4b, §8–§11) | 29 | the twin could serve, but these harnesses are Amaranth + Wishbone and the twin is Verilog. **This is the whole cost of retirement** |
| controller-only (X) | 24 | need a faithful device or none; no part fact claimed |
| structural (S) | 9 | source greps |
| caller-side fault injection (F caller) | 12 | belongs to the driving testbench |

**99 of 143 assertions have no route to `hyperram_model.v` as it stands**, and 54
of those need an Amaranth-side device of some kind. `ModelHyperRAM16` is not
merely a conformance liability; it is the memory array 29 SoC-layer checks read
their results out of.

## Deletable today, on this audit alone

| what | checks | why |
|---|---|---|
| the 16-bit re-read of the CA capture (§1) | 1 | tautological |
| "the read raises BURSTDET" (§2) | 1 | duplicate predicate; `burstdet` is never read |
| "the SHORT count would sample inside the latency window" (§2) | 1 | circular on the controller's own constant |
| the 49 CK / 304 CK totals (§8) | 2 | circular on the controller's own constant |
| `section_dqs_write_order` (§7b) | 0 | asserts nothing; reads as coverage |

Five assertions and one dead section. Everything else moves or stays.

## Sequencing, unchanged from #346

1. The bridge exists —
   [`controller_model_tb.sv`](../../../gateware/probes/hyperram/controller_model_tb.sv)
   + [`hyperram_model_sim.py`](../../../scripts/hyperram_model_sim.py), merged at
   `ba64439` — but it is a Verilog testbench, not a path from the Amaranth SoC
   harnesses. The 29 integration checks that use the Python model as an array
   still have nowhere to go.
2. **Done** — the three device-side knobs are on the twin, one commit each, with
   `--sim fault` as the regression.
3. Delete only what this audit lists as deletable, plus what the bridge has
   demonstrably taken over.
4. `soc_hyperram_sim.py` keeps I, X, S and the caller-side F — 99 assertions on
   today's count.
