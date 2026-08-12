# `soc_hyperram_sim.py`, check by check

Every assertion classified, and the classification is CHECKED:
[`../../../scripts/hyperram_sim_census.py`](../../../scripts/hyperram_sim_census.py)
reads the table below against the file and fails if an assertion is missing from
it, if a `caller` row has vanished, or if a `device`/`redundant`/`wrong` row is
asserted again. An assertion with no home is what made the first attempt at
[#346](https://github.com/awtoau/cynthion-workspace/issues/346) useless.

Baseline: **146 assertions** at `d154ff9`. Now **122**, of which 7 are new.

## The four classes

| class | meaning | what happened |
|---|---|---|
| **caller** | about the CONTROLLER or the SoC above it -- fault injection, the Wishbone window, the arbiter, coalescing, clock stop | stays |
| **device** | asserts a fact about the PART | moved to [`controller_model_tb.sv`](../../../gateware/probes/hyperram/controller_model_tb.sv) against [`hyperram_model.v`](../../../gateware/probes/hyperram/hyperram_model.v), and deleted here |
| **redundant** | already checked where the reference is independent | deleted |
| **wrong** | encodes a belief since refuted | deleted |

| class | count |
|---|---|
| caller | **115** kept, plus **7** written to replace the caller-side half of what moved |
| device | **19** |
| redundant | **11** |
| wrong | **1** |
| | **146** |

`caller` is not a residue. It is what the Python model is *for*: it can be told
to lie, and it sees the layers between the chip and the CPU. A faithful device
model can do neither.

## Where the 19 moved, and the defect each still fails on

Every moved check has a defect run in
[`hyperram_model_sim.py`](../../../scripts/hyperram_model_sim.py), injected on the
wire BETWEEN the controller and the device so the controller is untouched. The
run is required to produce the named line; a clean defect run exits non-zero.

| moved check | now | defect | what it prints |
|---|---|---|---|
| the five CA fields (§1) | case 2b, on the model's own capture and array | `+ca_defect=1` address bit 20 | `CA-FIELD FAIL memory write landed where it was addressed` |
| | | `+ca_defect=2` CA[45] | `CA-FIELD FAIL memory write command byte` |
| | | `+ca_defect=3` CA[46] | `CA-FIELD FAIL memory write command byte` |
| tCSHI, both controllers (§4, §4b) | `T_CSHI_NS` monitor on the twin | `+cs_hold_ns=25` | `ERROR tCSHI violation` |
| tCSM (§13) | the twin's existing tCSM monitor, `+stim=1` | `+cs_hold_ns=1000` | `ERROR tCSM violation` |
| the RWDS-over-CA sample (§14) | `+stim=2 -DCA_RWDS_FAULT=3` | `+rwds_float=1` | `DATA FAIL var code 2` |
| CA[45] for register space (§15) | case 2b, command bytes 0x60 and 0x80 | `+ca_defect=2` | `CA-FIELD FAIL` |

Calibration, measured 2026-08-10: the controller leaves a **30 ns** gap at 100 MHz
and tCSHI is **6 ns** at T166, so `+cs_hold_ns=24` leaves exactly 6 ns and does
NOT violate, 25 does. The knob is in nanoseconds for that reason -- a whole-cycle
knob either changes nothing or swallows the gap entirely, and then the device
never sees CS# rise and the monitor cannot fire at all. That was the first
version, and it exited 0 having proved nothing.

## The one that was wrong

`a count below 2 is clamped rather than wrapped` (§5b). The read latency floor is
**`R + 3`**, not 2 -- derived twice on 2026-08-10, once from a simulated burst
([#353](https://github.com/awtoau/cynthion-workspace/issues/353)) and once from
Winbond's AC parameters ([`config-ac.md`](config-ac.md)). Below it `READ_DATA`
begins while the device's RWDS fall is still in flight and latches it as a read
strobe over a tristate bus.

Replaced by a check that compares `latency_clocks = 0` against the floor itself,
which fails if the floor is 2.

## Section 7b asserted nothing

`section_dqs_write_order` ran, printed, and checked nothing: `jtag_ack` never
arrived, so nothing reached the model and it reported on an empty capture. It
counted as coverage in the section list. Deleted; the question -- which 16-bit
half of a DQS write reaches the device first -- is
`hyperram_dqs_model_sim.py --stage order`'s, against the twin and Winbond's model,
with a deliberately rewired run required to fail.

## What has no independent judge

* **The DQS controller's tCSHI and tCSM.** `controller_model_tb.sv` drives the
  non-DQS controller. Both controllers do the same `_recovery_cycles` arithmetic,
  so the NUMBER is judged; the DQS FSM holding it is not.
  [#371](https://github.com/awtoau/cynthion-workspace/issues/371).
* **tCK maximum**, the stall bound in §11. Arithmetic against a documented
  number, with no model that has a tCK maximum to contradict it.

## The table

| section | assertion | class | where it lives now |
|---|---|---|---|
| `section_as_built` | [completed] as built | caller | stays |
| `section_as_built` | as built: a read issues a command the device decodes | caller | stays |
| `section_clock_stop` | a coalesced line write is correct once CK can stop | caller | stays |
| `section_clock_stop` | ...in 32 device words, not the ungated 48 | caller | stays |
| `section_clock_stop` | ...at the address each beat asked for | caller | stays |
| `section_clock_stop` | the line is ONE transaction per direction, not sixteen | caller | stays |
| `section_clock_stop` | the gated line costs no extra CK, and a read saves one | caller | stays |
| `section_clock_stop` | a coalesced line read returns all sixteen beats | caller | stays |
| `section_clock_stop` | ...in one transaction | caller | stays |
| `section_clock_stop` | every withheld clock fell inside the device's data phase | caller | stays |
| `section_clock_stop` | one withheld clock per beat, 15 writing and 16 reading | caller | stays |
| `section_clock_stop` | the same run without the gate still reproduces the board | caller | stays |
| `section_clock_stop` | gating CK level with the word stall corrupts EVERY beat | caller | stays |
| `section_clock_stop` | ...and does not even touch 32 device words | caller | stays |
| `section_clock_stop` | the stall bound is inside rev A01-006's 100 ns tCK maximum | caller | stays |
| `section_clock_stop` | ...and the bubble it has to cover is one cycle, well inside it | caller | stays |
| `section_command` | [completed] the command read | caller | stays |
| `section_command` | one command was issued | caller | stays |
| `section_escape` | upstream's non-DQS READ_DATA never returns from a silent device | caller | stays |
| `section_escape` | ...nor from a device that delivers 7 of the 8 beats asked for | caller | stays |
| `section_escape` | ...and upstream's WRITE_DATA never returns from a stalled consumer | caller | stays |
| `section_escape` | [completed] non-DQS, silent device | caller | stays |
| `section_escape` | ...and `timed_out` says the watchdog ended it, not the caller | caller | stays |
| `section_escape` | [completed] non-DQS, 7 of 8 beats | caller | stays |
| `section_escape` | ...having taken the 7 beats the device did deliver | caller | stays |
| `section_escape` | ...and flagged the transaction | caller | stays |
| `section_escape` | [completed] non-DQS, 8 of 8 beats | caller | stays |
| `section_escape` | 8 of 8 ends on `final_word`, with NOTHING flagged | caller | stays |
| `section_escape` | ...and ends sooner than the watchdog would have | caller | stays |
| `section_escape` | [completed] non-DQS, consumer that stops asking | caller | stays |
| `section_escape` | ...a write nobody ends is flagged too | caller | stays |
| `section_escape` | [completed] non-DQS register write | caller | stays |
| `section_escape` | ...and a register write ends on its own, unflagged | caller | stays |
| `section_escape` | [completed] non-DQS register read, silent device | caller | stays |
| `section_escape` | upstream's DQS READ_DATA never returns from a silent device | caller | stays |
| `section_escape` | ...nor its WRITE_DATA from a stalled consumer | caller | stays |
| `section_escape` | [completed] DQS, silent device | caller | stays |
| `section_escape` | ...and the DQS controller flags it | caller | stays |
| `section_escape` | [completed] DQS, consumer that stops asking | caller | stays |
| `section_escape` | [completed] DQS register write | caller | stays |
| `section_escape` | ...unflagged, since the caller's own path ended it | caller | stays |
| `section_held` | held: the word arrives at the address asked for | caller | stays |
| `section_held` | pulsed `perform_write`/`write_data`: the device does NOT get it | caller | stays |
| `section_held` | pulsed `final_word`: the burst does not end where it was meant to | caller | stays |
| `section_held` | [completed] pulsed `final_word` | caller | stays |
| `section_latency` | [completed] the fixed-latency read | caller | stays |
| `section_latency` | upstream forces the long branch unconditionally | caller | stays |
| `section_latency_input` | an undriven `latency_clocks` still reaches the data body | caller | stays |
| `section_latency_input` | undriven matches the build-time constant exactly | caller | stays |
| `section_latency_input` | each latency setting waits a different number of cycles | caller | stays |
| `section_latency_input` | the wait tracks the setting one for one | caller | stays |
| `section_latency_input` | a count under the read floor is clamped up to it, not wrapped | caller | stays |
| `section_latency_input` | `fixed_latency` is an input, not a compile-time constant | caller | stays |
| `section_line_read_bubble` | coalescing across a bubbling master also corrupts READS | caller | stays |
| `section_line_read_bubble` | the shipping window returns all sixteen beats | caller | stays |
| `section_line_refill` | a 16-beat incrementing burst issues ONE HyperBus transaction | caller | stays |
| `section_line_refill` | the pre-change classic arrangement issues SIXTEEN transactions | caller | stays |
| `section_line_refill` | the coalesced refill returns all sixteen Wishbone beats | caller | stays |
| `section_line_refill` | the classic negative control returns the same sixteen beats | caller | stays |
| `section_line_refill` | <f-string> | caller | stays |
| `section_line_refill` | the old fixed 748-word cap would NOT have fitted at this CK | caller | stays |
| `section_line_refill` | a 64-byte line is well inside the cap | caller | stays |
| `section_line_write` | a single 32-bit write stores exactly two device words | caller | stays |
| `section_line_write` | ...at the doubled address, low half first | caller | stays |
| `section_line_write` | coalescing across a bubbling master reproduces the BOARD | caller | stays |
| `section_line_write` | ...with the board's first bad beat, halves transposed | caller | stays |
| `section_line_write` | ...and 48 device words written for a 32-word line | caller | stays |
| `section_line_write` | the shipping window writes all sixteen beats correctly | caller | stays |
| `section_line_write` | a 64-byte line touches 32 device words and not one more | caller | stays |
| `section_line_write` | every word lands at the address the beat asked for | caller | stays |
| `section_line_write` | one HyperBus transaction per beat, since none may be held open | caller | stays |
| `section_recovery` | upstream's RECOVERY state is still a TODO | caller | stays |
| `section_recovery` | upstream's controller leaves ONE cycle and nothing more | caller | stays |
| `section_recovery` | [completed] back-to-back DQS reads | caller | stays |
| `section_recovery` | ...and the gap is the count RECOVERY was given | caller | stays |
| `section_recovery_non_dqs` | upstream's non-DQS controller leaves LESS than the count needs | caller | stays |
| `section_recovery_non_dqs` | ...and it is the GAP that differs, not the transaction count | caller | stays |
| `section_recovery_non_dqs` | [completed] back-to-back non-DQS reads | caller | stays |
| `section_recovery_non_dqs` | the vendored controller leaves its whole count, unaided | caller | stays |
| `section_recovery_non_dqs` | ...and still issues both transactions | caller | stays |
| `section_recovery_non_dqs` | sixteen classic transactions through the window keep the same gap | caller | stays |
| `section_register_clock_stop` | the same stall DOES withhold clocks from a memory access | caller | stays |
| `section_register_clock_stop` | a register WRITE keeps its clock under a stalling master | caller | stays |
| `section_register_clock_stop` | a register READ keeps its clock under a stalling master | caller | stays |
| `section_register_clock_stop` | ...and both register transactions still complete | caller | stays |
| `section_register_clock_stop` | ...by serving the transaction, not by the tCSM watchdog | caller | stays |
| `section_shared_engine` | the shared engine starts a Wishbone read | caller | stays |
| `section_shared_engine` | a read starts at the doubled address with final_word low | caller | stays |
| `section_shared_engine` | final_word is low for the first read word | caller | stays |
| `section_shared_engine` | final_word rises for the second read word | caller | stays |
| `section_shared_engine` | final_word stays high through read recovery | caller | stays |
| `section_shared_engine` | the shared engine returns both read words | caller | stays |
| `section_shared_engine` | the shared engine starts a Wishbone write | caller | stays |
| `section_shared_engine` | write controls begin held on the low half | caller | stays |
| `section_shared_engine` | write controls remain held for the first word | caller | stays |
| `section_shared_engine` | the second word carries the upper half and final_word | caller | stays |
| `section_shared_engine` | write controls stay held through recovery | caller | stays |
| `section_shared_engine` | the write acknowledges after both words | caller | stays |
| `section_structural` | upstream assigns bus.clk as a single net | caller | stays |
| `section_structural` | ours drives the differential clock's TRUE pin only | caller | stays |
| `section_structural` | ours drives RESET#, which upstream leaves floating | caller | stays |
| `section_structural` | ours takes the polarity from the resource, not from a literal | caller | stays |
| `section_structural` | ours needs the `fast` domain, and says so | caller | stays |
| `section_structural` | ours keeps upstream's controller rather than copying it | caller | stays |
| `section_tcsm` | upstream holds CS# Low until the harness gives up | caller | stays |
| `section_tcsm` | ours chops it inside the budget the controller computed | caller | stays |
| `section_tcsm` | ...at the word cap exactly, not one beat past it | caller | stays |
| `section_tcsm` | ...and the caller is told the controller ended it | caller | stays |
| `section_tcsm` | the DQS controller chops the same shape | caller | stays |
| `section_wishbone` | pulsing a request before a delayed grant completes NOTHING | caller | stays |
| `section_wishbone` | the real port holds its request until the delayed grant | caller | stays |
| `section_wishbone` | Wishbone word addresses become 16-bit HyperRAM addresses | caller | stays |
| `section_wishbone` | a Wishbone read reaches the controller as a read | caller | stays |
| `section_wishbone` | two 16-bit words return one little-endian 32-bit word | caller | stays |
| `section_wishbone` | a full store stays a write and holds all 32 data bits | caller | stays |
| `section_wishbone` | a full store acknowledges after its second word | caller | stays |
| `section_wishbone` | a partial store starts with a read | caller | stays |
| `section_wishbone` | a partial store changes to a write after the merge | caller | stays |
| `section_wishbone` | inactive byte lanes survive the partial-store merge | caller | stays |
| `section_wishbone` | the read half of a partial store does not acknowledge early | caller | stays |
| `section_wishbone` | the write half of a partial store completes the request | caller | stays |
| `section_wishbone` | a missing EOB is forced closed at the tCSM-safe cap | caller | stays |
| `section_ca_rwds` | a level left over from before the CA does NOT extend it | device | `+stim=2 -DCA_RWDS_FAULT=3`, control `+rwds_float=1` |
| `section_command` | the device decodes the address that was asked for | device | case 2b of `controller_model_tb.sv`, at 0x35a1c7 |
| `section_command` | it decodes as a read | device | case 2b of `controller_model_tb.sv`, command byte 0x20 / 0xa0 |
| `section_command` | it decodes as memory, not register space | device | case 2b of `controller_model_tb.sv`, 0xa0 against 0xe0 |
| `section_command` | it decodes as a linear burst | device | case 2b of `controller_model_tb.sv`, 0xa0 against 0x80 |
| `section_command` | the command matches the specification's encoding | device | case 2b of `controller_model_tb.sv`, all four bytes |
| `section_recovery` | the vendored controller keeps tCSHI with NO gap from the master | device | `hyperram_model.v`'s tCSHI monitor, `+cs_hold_ns=25` |
| `section_recovery_non_dqs` | upstream's non-DQS controller VIOLATES tCSHI back-to-back | device | `hyperram_model.v`'s tCSHI monitor, `+cs_hold_ns=25` |
| `section_recovery_non_dqs` | the vendored controller keeps tCSHI with NO gap from the master | device | `hyperram_model.v`'s tCSHI monitor, `+cs_hold_ns=25` |
| `section_recovery_non_dqs` | sixteen classic transactions through the window keep it too | device | the judgement to the twin; the gap comparison stays, re-based |
| `section_register_ca` | upstream emits CA[45]=0 for a single_page register write | device | case 2b of `controller_model_tb.sv`, 0x60 |
| `section_register_ca` | ours forces CA[45]=1 there | device | case 2b of `controller_model_tb.sv`, 0x60 |
| `section_register_ca` | ...which is the datasheet's 0x60 for a register write | device | case 2b of `controller_model_tb.sv`, 0x60 |
| `section_register_ca` | a WRAPPED memory burst is left alone -- CA[45]=0 | device | case 2b of `controller_model_tb.sv`, 0x80 |
| `section_register_ca` | the DQS controller forces it too | device | case 2b of `controller_model_tb.sv`; the DQS path is #371 |
| `section_tcsm` | upstream holds CS# Low past tCSM on a burst nobody ends | device | `hyperram_model.v`'s tCSM monitor, `+stim=1 +cs_hold_ns=1000` |
| `section_tcsm` | ours chops it, and CS# never passes tCSM | device | `hyperram_model.v`'s tCSM monitor, `+stim=1` |
| `section_tcsm` | a silent device does not hold CS# past tCSM either | device | `+stim=1 -DDELIVER_WORDS=0`, tCSM judged by the device |
| `section_tcsm` | the DQS controller keeps tCSM on the same shape | device | the judgement to the twin; the budget comparison stays, re-based |
| `section_ca_rwds` | RWDS raised mid-CA takes the LONG latency | redundant | case 4 with `REFRESH_EVERY=1`, the device asking for real |
| `section_ca_rwds` | ...and the two choices are distinguishable at all | redundant | guard for the two rows above; the sweep prints both counts |
| `section_command` | reading the same capture 16 bits wide gives a WRONG address | redundant | cannot fail -- a property of `TEST_ADDRESS`; case 2b of `controller_model_tb.sv` |
| `section_latency` | the controller's two latency counts differ | redundant | `controller_model_tb.sv` cases 3 and 4, six codes each |
| `section_latency` | a read against the fixed-latency model returns data | redundant | the DATA checks of cases 3 and 4 |
| `section_latency` | the read raises BURSTDET, so DQS is what found the data | redundant | same predicate as the row above; `burstdet` is never read |
| `section_latency` | the SHORT count would sample inside the latency window | redundant | CTRL-BEAT, and `--negative-control` proves 1 CK is caught |
| `section_latency_input` | driving `fixed_latency` low reaches the variable branch | redundant | cases 3 and 4 sweep both modes against the twin |
| `section_line_refill` | one line occupies 49 CK with command and fixed latency | redundant | circular on `HIGH_LATENCY_CLOCKS`; CTRL-BEAT grades the beat |
| `section_line_refill` | sixteen classic transfers occupy 304 CK | redundant | circular on `HIGH_LATENCY_CLOCKS`; CTRL-BEAT grades the beat |
| `section_recovery_non_dqs` | ...at the addresses asked for | redundant | case 2b of `controller_model_tb.sv` and case 2 |
| `section_latency_input` | a count below 2 is clamped rather than wrapped | wrong | the read floor is `R + 3`, not 2 (#353, #372) -- replaced |

## Running it

    scripts/hyperram_sim_census.py            # the table against the file
    scripts/hyperram_sim_census.py --list     # print the classification

    scripts/soc_hyperram_sim.py                        # 122 checks, caller-side
    scripts/hyperram_model_sim.py                      # the moved ones + 6 defect runs
    scripts/hyperram_model_sim.py --negative-control   # 1 CK of latency error
    scripts/hyperram_vendor_model_sim.py --sim icarus  # the twin against the vendor tb
    scripts/hyperram_dqs_model_sim.py --stage all      # the DQS path
