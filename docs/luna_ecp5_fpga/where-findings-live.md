# Where the ECP5 findings live

The ECP5 work split into two kinds, and they belong in different repositories.

The test: **would this be useful to someone with a different ECP5 board?**

## In pluribus (`docs/ecp5/`)

Toolchain findings — true of the ECP5 and its tools regardless of hardware:

| doc | subject |
|---|---|
| `toolchain-gap-findings.md` | `BASE_TYPE` degeneracy; the four-layer gap matrix |
| `sedga-findings.md` | SEDGA's encoding was already in prjtrellis |
| `diamond-family-trap.md` | `ep5c00` is LatticeECP3, not ECP5 |
| `ecp5-primitive-coverage.md` | which introspection primitives the open flow supports |
| `ecp5-real-world-corpus.md` | 243 bitstreams, and what a self-built corpus cannot reveal |
| `README.md` | index, issues #85-#90, and what blocks pushing |

## Here

Board findings — things that depend on Cynthion r1.4, Apollo, or this design.

**Programming and configuration:**

- **`../apollo_samd11_mcu/apollo-configure-speed-investigation.md` — the single
  document for JTAG configuration speed.** It lives with the Apollo firmware docs
  because that is what it changes; it is indexed here because the work started here.
  Final result **713.9 → 322.2 ms, 2.22x**, shipped. The remaining time is USB, not
  JTAG: the transport costs 3.9x what the bits do. SCK cannot be raised — the divider
  steps 12 to 24 MHz with nothing between, and the SAMD11 is rated to 11.9 MHz.
  Includes the synthetic (no-USB) benchmark, the DMA result, every failed attempt, and
  recovering a clean state between runs.

  **Do not start a second document on this topic.** Four accumulated and three had to
  be retired; two of them stated conclusions that were the opposite of the truth, and
  one of those cost months. Add to the table in that file instead.
- `dynamic-opcode-probe.md` — the live-silicon opcode sweep. Its generic ECP5
  facts are summarised in pluribus's README; the Apollo specifics stay here.
- `flash-partitioning.md`, `reconfigure-initn-gap.md`, `flash-speed.md`,
  `spi-flash-summary.md` — flash, boot selection, and the INITN gap.

**Retired to `debris/docs/`** — kept for reasoning, wrong on their numbers or their
titles: `apollo-configure-speed.md`, `jtag_configure_bottleneck.md` (both predate the
fixed-payload benchmark, so their milliseconds are non-comparable) and
`dma-negative-result.md` (its title asserts the opposite of what is now measured).

**Optimisation and device fit:**

- `bram-budget.md` — who actually uses block RAM. The analyzer uses 9 of 56; the
  heavy consumers are soft CPUs, and it is firmware storage rather than buffers.
- `hyperram-speed.md`, `usb-performance.md` — measured throughput, and the
  measurement traps encountered getting them.

## One finding worth not losing

**The ECP5 has DSP blocks, so a hardware multiplier is cheap.** Recorded in
`docs/moondancer/riscv_state_of_play.md` as a reason to keep `mul` in a soft-CPU
build rather than falling back to software.

Confirmed by measurement: integer multiply costs **16 cycles** against 123 for a
soft-float single-precision multiply. That is the DSP blocks doing the work, and
it is why `rv32im` earns its area on this part.
