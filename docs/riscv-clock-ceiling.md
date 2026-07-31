# The RISC-V SoC clock ceiling: it was never place-and-route

Issue #110 asked whether Lattice Diamond's place-and-route reaches a higher
clock than nextpnr on the VexiiRiscv SoC. The premise was that nextpnr's
achieved frequency climbs with the requested one -- 72.6 at 60, 76.3 at 80,
86.1 at 90, 92.0 at 100 -- so ~92 MHz looked like where nextpnr stopped trying
rather than where the silicon stopped working.

**The premise was right and the question was aimed at the wrong stage.** The
ceiling is not placement. Two separate things cap this design, and neither is
the placer:

1. a **PLL divider bug** that silently breaks USB at almost every frequency
2. a **real fabric limit** somewhere below 92 MHz, which the CPU crosses by
   corrupting its output rather than stopping

## 1. Only three frequencies in 60..130 MHz can work at all

`VariableClockDomainGenerator` derives `usb` from the PLL's VCO by an **integer**
division:

    usb_div = int(round(vco_mhz / 60.0))

The ULPI PHY requires 60 MHz. That division only yields exactly 60 when the VCO
is a whole multiple of 60, and usually it is not:

| sync | VCO | usb_div | usb | error | usable |
|---|---|---|---|---|---|
| 60 | 600.0 | 10 | **60.000** | +0.00% | yes |
| 80 | 560.0 | 9 | 62.222 | +3.70% | no |
| 90 | 630.0 | 10 | 63.000 | +5.00% | no |
| 100 | 600.0 | 10 | **60.000** | +0.00% | yes |
| 110 | 550.0 | 9 | 61.111 | +1.85% | no |
| 120 | 600.0 | 10 | **60.000** | +0.00% | yes |

Across 60..130 MHz in 2 MHz steps, **only 60, 100 and 120 land on an exact 60
MHz `usb` clock.** Every other value ships a PHY clock that is wrong by 1-5%.

This is not a derivation on paper. nextpnr states it in its own log for each
build, which is where it was confirmed rather than assumed:

    90/top.tim:  Derived frequency constraint of 63.0 MHz for net aux_phy_0__clk__o
    100/top.tim: Derived frequency constraint of 60.0 MHz for net aux_phy_0__clk__o
    110/top.tim: Derived frequency constraint of 61.1 MHz for net aux_phy_0__clk__o

And the board agrees. A 90 MHz build places, packs and configures without
complaint, and then **never appears on the USB bus at all** -- while a 100 MHz
build, a *higher* CPU clock, enumerates immediately. A ULPI link is
source-synchronous parallel signalling with no start bit and no resynchronising
mechanism, so it has no tolerance to spend; 5% is not "slightly off", it is a
dead interface.

The failure mode is the expensive part: a design with a broken PHY clock looks
exactly like a design that missed timing. Both produce silence. That is what
made ~90 MHz look like a placement ceiling.

`variable_clock.py` now **refuses to build** outside 0.5%, naming the arithmetic
and the usable frequencies, rather than emitting a bitstream that cannot work.

This also retires one row of the original table: the 80 MHz build that
"succeeded" had `usb` at 62.2 MHz, so it could never have enumerated. Passing
timing was never the same as working.

## 2. Above 60 MHz the CPU corrupts its output rather than stopping

nextpnr's build script runs under `set -e` with no `--timing-allow-fail`, so a
design that misses its constraint stops the build. That is a refusal to vouch
for a placement, not an inability to produce one -- and nextpnr's static timing
analysis is a worst-case-corner bound that real silicon at room temperature
routinely beats. So "refused at 90" left the interesting question untouched.

`./scripts/nextpnr_allow_fail_ladder.py` re-places the same netlist with
`--timing-allow-fail`, packs it, and puts it on the board. Placement effort is
identical; only the verdict changes.

| requested | nextpnr achieved | bitstream | usb clock | on hardware |
|---|---|---|---|---|
| 60 | 72.6 / 89.0 MHz | yes | 60.000 | **PASS** -- product `369d0368`, ticks 0 -> 1 |
| 90 | 86.1 MHz | yes | 63.000 | does not enumerate (PHY clock, not timing) |
| 100 | 92.0 MHz | yes | 60.000 | enumerates, **output corrupted** |
| 110 | 96.0 MHz | yes | 61.111 | enumerates, **output corrupted** |

The corruption is the finding. At 100 MHz the console prints:

    tick00001
    tk00002
    rck 000003

and at 110 MHz:

    tck 000
    ik00002
    ic 000

The counter still increments -- 1, 2, 3 -- so the CPU is **executing**, fetching,
looping and incrementing. But characters are dropped and mangled. This is
exactly the predicted failure of a marginal design: it does not halt, it
computes and transfers wrongly. A check that only asked "did the device
enumerate" would have scored both of these as passes.

Note that 110 MHz corrupts *and* has a 1.85% PHY error, so it fails twice over;
100 MHz has a perfect PHY clock and still corrupts, which is what isolates the
fault to the design's own logic rather than to USB.

## 3. Diamond could not answer the question that was asked

Two Diamond configurations exist, and the informative one is unavailable.

**PAR isolation (`--mode yosys`) remains structurally blocked.** Feeding the
*same yosys netlist* into Diamond's place-and-route is what would isolate PAR
from synthesis. It was re-checked rather than inherited from the earlier
finding, and `ngdbuild` still rejects the netlist with exactly the two
documented error classes and no new ones:

    ERROR - ngdbuild: Block console.fifo.r_data_LUT4_Z_6: missing INITSTATE property on ROM .
    ERROR - ngdbuild: INITVAL string not allowed on single-port or dual-port block
                      cpu...regfile_fpga.asMem_ram.1.9(TRELLIS_DPR16X4)

yosys emits Project Trellis's primitive vocabulary (`TRELLIS_DPR16X4`,
`TRELLIS_FF`, LUT4 `INIT`); Diamond's library has none of those cells. The two
toolchains meet at the bitstream, not at the netlist. No fourth blocker has
appeared -- the three documented handoff bugs are still worked around
successfully, and this is the wall behind them. See
`/mnt/2tb/git/pluribus/docs/ecp5/diamond-par-isolation-blocked.md`.

**Whole-toolchain (`--mode lse`) did not complete.** Diamond's LSE synthesis was
stopped after **21 minutes 23 seconds at 98-99% CPU without emitting a
netlist** -- no `.ngd`, so map, par, trce and bitgen were never reached. The
entire yosys + nextpnr flow on the same RTL takes roughly 20 seconds, so this is
over 60x the whole open flow spent on synthesis alone, still unfinished. The
earlier measurement of ~7x on the smaller analyzer design was optimistic for
this one.

That is a bounded negative rather than a partial result: no Diamond frequency
figure exists for this design, claimed or verified, because Diamond never
produced anything to measure. It was stopped deliberately rather than left to
run, per the standing instruction not to spend unbounded effort on the handoff.

One handoff detail is worth recording for anyone repeating this: `behavioural.v`
already *contains* the VexiiRiscv module, because yosys read the pre-generated
core in and re-emitted it. Passing `VexiiRiscv.v` again as an extra source stops
LSE immediately:

    ERROR - synthesis: extra0.v(11603): duplicate module name VexiiRiscv. VERI-1206

### The frequency table, as asked for

| requested | nextpnr claimed | nextpnr verified | Diamond claimed | Diamond verified |
|---|---|---|---|---|
| 60 | 89.0 MHz | **PASS** | -- | -- |
| 90 | 86.1 MHz | no enumeration (PHY clock) | none (synthesis unfinished) | -- |
| 100 | 92.0 MHz | enumerates, output corrupt | none (synthesis unfinished) | -- |
| 110 | 96.0 MHz | enumerates, output corrupt | none (synthesis unfinished) | -- |

Diamond built **nothing** at any frequency, so it did not build where nextpnr
refused. The comparison was therefore not obtained -- and it was also rendered
moot, because the ceiling turned out not to be place-and-route.

## What #110 should do now

**The open flow is not the constraint, so switching toolchains does not help.**
Even had Diamond placed 5% faster, both frequencies above 60 that a Diamond
bitstream could use -- 100 and 120 -- are ones where this CPU already computes
wrongly at nextpnr's own 92 MHz placement. A better placer does not fix a design
that corrupts data at the clock it is given.

The order of work that follows from this:

1. **Fix the PLL divider** (done -- it now refuses rather than shipping a dead
   PHY). Any future ladder must step 60 -> 100 -> 120 and nothing between,
   because nothing between can work.
2. **Find what corrupts at 92 MHz.** The counter advances while characters
   drop, which points at the console FIFO / USB path rather than at the CPU
   core -- a CPU miscomputing would give wrong tick *values*, not missing
   characters. That is a specific, testable next question.
3. **Re-measure the true ceiling at 100 MHz only**, once (2) is understood.

## Reproducing

    ./scripts/nextpnr_allow_fail_ladder.py --frequencies 90 100 110
    ./scripts/riscv_verify_bitstream.py tmp/nextpnr_allow_fail/100/top.bit
    ./scripts/diamond_riscv_ladder.py --check-edif --frequencies 100

## A note on the measurement harness

The first pass of this experiment reported `*** FAIL  no console tty appeared`
at 90, 100 and 110 MHz -- **and also failed a known-good 60 MHz control.** A
test that fails its own control is measuring the test.

Two distinct bugs were behind it, and both are worth knowing:

- **The boot banner is transient.** The firmware prints its product line once,
  at boot. Enumeration after a reconfigure takes about 0.47 s (measured), and
  the banner is emitted inside that window. Any check that configures, waits for
  the tty, and only then opens the port has already missed it -- and then
  reports "arithmetic is wrong" for a healthy board.
- **Holding the port open across the reconfigure does not work either.** The
  device re-enumerates, the kernel tears the old node down, and the next read
  raises `SerialException` on a node that no longer backs a device.

`./scripts/riscv_verify_bitstream.py` reopens as fast as the node appears and
reports **INCONCLUSIVE** when it loses that race, rather than scoring a missed
banner as a wrong answer. Absence of evidence gets said out loud. It passes the
60 MHz control, which is the property the earlier harness lacked.
