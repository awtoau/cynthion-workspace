# SoC clocking: a PLL divider bug, and a fabric limit the CPU crosses silently

Two findings, either of which will cost a day if rediscovered:

1. A **PLL divider bug** silently breaks USB at almost every frequency in
   60–130 MHz. Only three are safe.
2. The **fabric limit sits below 92 MHz**, and the CPU crosses it by *corrupting
   its output* rather than by stopping — so a design that is over the limit looks
   like it works.

Issue #110 asked whether Lattice Diamond's place-and-route reaches a higher clock
than nextpnr on the VexiiRiscv SoC. The premise was that nextpnr's
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

## Diamond, and what #110 asked

The comparison against Lattice Diamond, why it could not answer the question as
posed, and what to do about #110 are recorded in the issue rather than here.
This file is for the two findings above, which outlive it.

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
