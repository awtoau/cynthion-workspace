
> **WITHDRAWN — every DQS figure below was measured with faulty instruments.**
> The pattern used only the low 16 address bits and so repeated 64 times across
> the part; the controller ran luna's `HIGH_LATENCY_CLOCKS = 5`, below the
> minimum of 6 that CR0's 14 CK requires, so reads landed by count rather than
> by strobe; the JTAG register readback slips below a `sync`/TCK ratio of about
> 4; and the negative control armed while the engine was already running.
>
> Re-measured with all four fixed, the DQS ceiling is **CK 140 at 238.9 MB/s
> read**, and **CK 180 fails in bulk with 4.7 M errors** — so "313.5 MB/s, DQS
> clean" is not merely unverified, it is wrong. `scripts/hyperram_ceiling.py`,
> and see #186/#188.
`greatscottgadgets/cynthion#147` "Add DQS support for HyperRAM" (mossmann) has
been open since **2024-07-16 with zero comments**, while
`greatscottgadgets/luna#236` *added* `HyperRAMDQSPHY` in May 2024.

We now know why the gap exists, and we have the measurements to close it.

## Why #147 has not moved

**`HyperRAMDQSPHY` cannot be instantiated on Amaranth 0.5.** It is written
against the 0.4 record API and raises before it elaborates:

    AttributeError: 'Pin' object has no attribute 'io'

Three faults, all in the I/O layer, none in the protocol:

1. `bus.dq.io` / `bus.rwds.io` exist only on a raw (`dir="-"`) request
2. `o_Z=self.bus.clk` and `o_Z=self.bus.cs` drive pads from inside a `DELAYG`
3. `bus.reset` is never driven — RESET# floats

Nothing in luna constructs it and nothing declares a `ram` resource for it, so
it has not been exercised since it landed.

**And there is a second blocker nobody has named.** `HyperRAMDQSPHY` reads
`ClockSignal("fast")` in every gearing primitive, and upstream has no clock
generator that can produce a `fast` domain at 2x `sync`. Ours can —
`VariableClockDomainGenerator`, which does not exist upstream at all.

## What we measured

Same board, same gateware pattern engine, 50M words per rung, both PHYs:

| PHY | CK | fabric `sync` | timing | read | errors |
|---|---|---|---|---|---|
| non-DQS | 120 | 120 | MET 135.9 | 198.2 MB/s | 0 |
| non-DQS | 140 | 140 | MET 143.2 | 229.7 MB/s | 0 |
| non-DQS | 150 | 150 | **FAIL 139.3** | — | — |
| non-DQS | 180 | 180 | **FAIL 134.6** | — | — |
| DQS | 160 | 80 | MET 121.9 | 278.6 MB/s | 0 |
| **DQS** | **180** | **90** | MET 124.9 | **313.5 MB/s** | 0 |
| DQS | 200 | 100 | MET | — | 43,360,384 |

**The point is not the MB/s.** The non-DQS PHY clocks the *fabric* at CK, so
CK 150+ cannot close on this ECP5 at all — that is nextpnr refusing, not the
HyperRAM. DQS clocks the fabric at CK/2, so CK 180 asks 90 MHz and closes with
35 MHz of margin. **DQS decouples the device clock from the fabric clock**, and
the non-DQS path cannot reach these rates at any effort level.

Against what Cynthion ships (CK 120): **1.58x**. Against the non-DQS PHY's own
fabric-limited ceiling (CK 140): **1.37x**.

## The contribution, as separable pieces

| | repo | blocking |
|---|---|---|
| port `HyperRAMDQSPHY` to Amaranth 0.5 | luna | **yes** |
| a clock generator that yields a `fast` domain at 2x | apollo | **yes** |
| `READCLKSEL` as a parameter (closes their `psram.py:110` TODO) | luna | no |
| `RECOVERY` is unimplemented (`psram.py:232` TODO) | luna | no |
| the dead low-latency branch (`psram.py:173` FIXME) | luna | no |
| the measurement harness, so they can reproduce this | cynthion | no |

Small and separate on purpose: each is one fact, and none asks them to adopt our
design.

## What we must not claim

DQS is **not** integrated end-to-end here. Our own Wishbone window reads one word
late (#186) — our unfinished plumbing, not a property of the PHY, and not the
configuration `analyzer/fifo.py` uses. The measurement above is a streaming
consumer driving `HyperRAMInterface` directly, which is the shape #147 cares
about.

## Steps

- [ ] Create real forks — `awtoau/*` are independent repos, not GitHub forks, so
      a PR cannot be opened from them. Existing repos stay untouched.
- [ ] Stage each piece as a branch with one focused commit
- [ ] Comment on #147 with the Amaranth 0.5 blocker and the measurements — that
      is useful to them whether or not they want our code
- [ ] Open the PRs, smallest first

Blocked on nothing. #186 is independent — it is our integration, not the PHY.
