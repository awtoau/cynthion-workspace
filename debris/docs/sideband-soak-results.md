# Sideband soak: the RC hypothesis is wrong, and slower is worse

**Retired 2026-08-05.** The result is folded into
[`docs/sideband.md` §2](../../docs/sideband.md#2-rate-230400-and-faster-is-better);
this file keeps the reasoning behind it. The script moved to
`debris/scripts/sideband_soak.py`.

Soak across baud and drive style, each direction scored separately
(`debris/scripts/sideband_soak.py`). Run after the drive style changed to open-drain at
both ends to remove a measured 30.4 us driver-to-driver short.

The question was whether open-drain still closes: it replaces an actively driven
rising edge with an RC against two internal pull-ups, estimated 0.3-1.5 us, which
is 7-35% of a bit at 230400. Arithmetic could not settle it.

## Result

| baud | style | FPGA→Apollo ok | short | CRC | Apollo→FPGA CRC | timeout | verdict |
|---|---|---|---|---|---|---|---|
| 115200 | open-drain | 4904 / 5000 | 96 | **0** | 0 | 98 | **FAIL** (98.1%) |
| 230400 | open-drain | **5000 / 5000** | 0 | 0 | 0 | 0 | **PASS** |

Remaining points were still running when this was written; the two above are
already decisive about the hypothesis.

## The RC hypothesis is refuted, not confirmed

**The slower rate fails and the faster one passes.** An RC rise-time limit cannot
produce that: a longer bit period gives the edge *more* time to reach threshold,
so open-drain should be strictly safer at 115200 than at 230400.

**Zero CRC errors at the failing rate.** Every byte that arrived was intact; the
failures are 96 short replies against 98 counted timeouts. Bytes never arrived at
all rather than arriving corrupted. A marginal rise time corrupts bytes — it does
not make them vanish.

So the open-drain change is not what fails here, and the estimated 0.3-1.5 us rise
is not the binding constraint at either rate.

## The real cause was already known, and this reproduces it

`fpga_adv.c:70-73` records it, from the original bring-up:

> transmit is bit-banged, so a byte occupies the CPU for 10 bit periods. At
> 115200 that is 86.8 us, at 230400 only 43.4 us -- halving the window in which a
> USB interrupt can preempt a bit and stretch it past the receiver's sample
> point. **100/100 at 230400 against 97/100 at 115200.**

That is the same result this soak measured, in the same direction, at 25 times the
sample count: 100% versus 98.1% here against 100% versus 97% there. An independent
reproduction of a prior finding rather than a new one.

**The mechanism is counter-intuitive and worth stating plainly: a slower baud is
worse, because each bit spends longer exposed to USB interrupt jitter.** Nothing
about the wire, the pull-ups or the drive style is involved.

## What this changes

**230400 is the correct rate**, and the agent's change of the `SidebandResponder`
and `sideband_debug.py` defaults from 115200 to 230400 aligns them with both the
firmware and the measurement. Those defaults were a live 2x mismatch against
firmware, not a theoretical one.

**The recommendation to "drop both sides to 115200 if open-drain proves marginal"
is wrong** and should not be followed. 115200 is the *worse* rate. If open-drain
ever does prove marginal, dropping the baud makes it worse on the jitter axis
while helping on the RC axis, and the jitter axis is the one that is actually
failing.

**460800 was already characterised as failing hard** — `fpga_adv.c:75`, 1/100 with
real CRC corruption rather than timeouts, because 104 CPU cycles per bit leaves
nothing after M0+ ISR entry and exit. That is a different failure signature from
115200's, and the soak covers it, so the two mechanisms can be told apart in the
same table.

## Why the control mattered

Push-pull is in the matrix as a control, not for completeness. It has no RC limit,
so a failure there is clocking or sampling. Without it, "open-drain fails at
115200" would have read as an RC result — which is precisely the wrong conclusion,
and the one the pre-soak arithmetic pointed at.

Divisor error is at most 0.16% at every point in the matrix, well inside the ~2%
UART budget, so nothing here can fail for clock-resolution reasons either.

## Still unverified

The rise time itself has not been observed. This soak shows it is not binding at
115200 or 230400, which is a weaker claim than measuring it. A scope on T6 would
give the number; the soak says the number is not currently the limit.
