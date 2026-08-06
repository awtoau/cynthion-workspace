## Measured on hardware: Active Clock Stop does not work, and the read gate is not why

The simulation says 16/16 in one transaction. The board says 1/16.

`clock_stop` and `sustained` both on, non-DQS at CK 60, sweeping the read gate's
delay 0-3 cycles:

| read delay | correct | bitmap | got | CK stalled |
|---|---|---|---|---|
| 0 | 1/16 | `1111111111111110` | `0f0e200f` | 2,754 cycles |
| 1 | 1/16 | `1111111111111110` | `0f0e200f` | 3,910 |
| 2 | 1/16 | `1111111111111110` | `0f0e200f` | 5,083 |
| 3 | 1/16 | `1111111111111110` | `0f0e200f` | 6,222 |

**The gate fires and the selector works** — stall cycles rise monotonically with
the delay. **The data does not respond at all.**

So read-gate alignment is not the fault. That was the one thing this experiment
existed to test, and it refutes the hypothesis in `soc-memory-bus.md` §5
that the read half needed only the same register the write half needed.

## What made this answerable

`probe_stall` already existed in `BootRAM` and had never been wired to a CSR.
Without a stall count, "the stall never fires" and "the stall is misaligned"
are the same observation — 1/16 either way — and they want opposite fixes. The
counter is now in `HyperRAMProbe` and `hr cross` reports it.

## Two things to carry forward

**The corruption changes shape.** Without clock stop it is 8/16 with the two
halves of every odd beat transposed. With it, 1/16 with a **+1 word shift** —
the same displacement #186 shows on the DQS path. Two different paths, one
signature. That may be a common cause or a coincidence; it is not established
either way.

**The simulation cannot arbitrate this.** Its model returns read data in the
same cycle as the CK that asked for it, which is exactly what silicon does not
do. Section 11 of `soc_hyperram_sim.py` is proof of MECHANISM only. Anything
further should be measured, not simulated.

## State

Both flags off again, for a measured reason rather than a cautious one. 16/16
and `ck-stalled 0` with them off, so the gate is inert when disabled rather than
merely quiet.

Next instrument is the ILA on CS#/CK/RWDS/DQ, which would serve #186 as well.
More CSR counters look unlikely to separate the remaining candidates: the write
path's timing, or CK-stop not doing mid-burst what §10.2.2 implies.
