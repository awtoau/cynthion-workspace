# Session audit: what was found, what was written down, what is stranded

Written 2026-07-30 by auditing the full session transcript (25 MB, 14079 JSONL
lines) with `scripts/transcript_grep.py`, because a summarised session keeps
conclusions and drops the measurements behind them.

The question asked was whether the ECP5 fuzzing discovery is documented and
whether the work generally survived. **The findings are documented. The code
mostly is not.**

## The headline problem: ten unmerged agent branches

Nine background agents committed work to their own worktree branches. Every
one is **unmerged, unpushed, and invisible from `main`**. `main` is at
`208a32b` and contains none of it.

| branch (agent) | commits | lines | what |
|---|---|---|---|
| `a2366741da283904f` | 5 | 3379 | Diamond 3.14 mining — 14 harvester scripts |
| `ac305391556e18911` | 1 | 1755 | dynamic opcode probe on live silicon |
| `aac790b33fef854f8` | 5 | 1706 | Diamond as an ECP5 oracle, pnr noise floor |
| `a1194bb205a461e34` | 1 | 1223 | SERCOM DMA implementation + measurements |
| `a877ddd89c5f0599c` | 1 | 1098 | flash partitioning, ECP5 boot selection |
| `a1bc45558ee043add` | 1 | 924 | fast loader, bitstream sink |
| `aff7b9b8a2a27e2c9` | 1 | 726 | quad-SPI boot, INITN blocker |
| `a41d45e8b0999eb54` | 2 | 754 | configure 2575 → 1680 ms |
| `a87bebab587ba6884` | 1 | 639 | JTAG measured without USB |
| `a3a542792badd9960` | 1 | 540 | configure is USB-bound, not clock-bound |

**Roughly 12700 lines across 19 commits.** The findings from these were
written into docs and issues; the scripts that produced them were not merged.
Anything needing re-measurement needs these branches first.

`git worktree list` still shows all ten, so nothing is lost *yet*. They are one
`git worktree prune` from being unreachable.

## The ECP5 fuzzing discovery

**It is documented**, in `pluribus/docs/ecp5/toolchain-gap-findings.md` on
branch `ecp5-commercial-corpus`, and filed as
[pluribus#85](https://github.com/awtoau/pluribus/issues/85). Summary here so
this repo's readers can find it; pluribus is the canonical home.

`PIOA.BASE_TYPE` holds the same 84 I/O-standard values in two tiles and
encodes them differently:

| tile | values | distinct encodings |
|---|---|---|
| `PICL0` | 84 | **3** |
| `PICL1` | 84 | **28** |
| `PICL0` `PIOB` | 40 | 2 |
| `PICL1` `PIOB` | 40 | 15 |

In `PICL0` every `INPUT_*` standard from LVCMOS12 to LVDS encodes to **zero
bits** — mutually indistinguishable in any bitstream. `PICL1` resolves the same
value set into 28 encodings and does tell LVCMOS12 from LVCMOS33 from SSTL15.

The asymmetry is what makes this **under-fuzzing rather than a hardware
limitation**: identical value sets on the same bel, and one tile shows what the
right answer looks like.

Scale: **1869 degenerate aliases on ECP5, 3044 on MachXO2** — and pluribus's
MachXO2 support is production, so it is decoding input standards by coin flip.

Re-verified directly against `share/trellis/database/ECP5/tiledata/*/bits.db`
rather than taken from the agent's report.

The detectors' credibility rests on mechanically reproducing two bugs
previously found by hand: the `PULLMODE`/`BASE_TYPE` overlap (D1,
`LLC3PIC_VREF3`, `F12B0`) and `EBR.MODE` (`DP8KC` indistinguishable from
`PDPW8KC`).

### Four other pluribus issues from the same work

[#86](https://github.com/awtoau/pluribus/issues/86) `trellis_unpack.py` cannot
decode ECP5 · [#87](https://github.com/awtoau/pluribus/issues/87) fuzz runner
hardcoded to MachXO2 · [#88](https://github.com/awtoau/pluribus/issues/88)
SEDGA gap is yosys/nextpnr, not encoding ·
[#89](https://github.com/awtoau/pluribus/issues/89) Lattice's own SEDGA docs
specify values Diamond rejects

## Two premises that were wrong, and one correction

Recorded because each was stated confidently before being checked.

**nextpnr-ecp5 already has full IOLOGIC support.** The MachXO2 gap does not
replicate. `pack.cc` (1717-2600) packs `IDDRX1F`, `ODDRX1F`, `ODDRX2F`,
`IDDRX2F`, `IDDR71B`, `ODDR71B`, `OSHX2A`, the DQS family, `DQSBUFM`,
`DELAYF/G`, `CLKDIVF`, `ECLKSYNCB`, `DDRDLLA`.

**`fuzzers/ECP5/105-sedga/` exists.** SEDGA's gap is one layer higher — yosys
never declares it in `cells_bb.v`. A much smaller fix than fuzzing it.

**The readback lead was a device-tree conflation.** `ReadBack FLASH,SRAM` and
`ReadCapture Disable,Enable` appear only under `or5g00`/`mg5g00`, which are
LatticeSC/ECP2-era trees. **No `ep5*` `bitgen.usg` documents either.** So the
ECP5 still has no configuration readback; the earlier reading was wrong about
which family it had found. Related: `ep5c00` is LatticeECP3, not ECP5 —
`sa5p00` is ECP5, and several findings had to be re-checked against the right
tree.

## The genuine IOLOGIC-shaped gap

`DLLDELD` is fuzzed (`132-dlldel`), declared by yosys, and present in
nextpnr's `constids.inc:1239` and `gfx.cc` — but `pack.cc`, `bitstream.cc`,
`cells.cc` and `arch.cc` never mention it. **Declared, drawable, unplaceable.**

`PUR` is worse: yosys declares it and nextpnr has no constid at all.

Six `bitgen -g` settings are ECP5-documented and absent from every open tool:
`CfgMode`, `DONEPHASE`, `GOEPHASE`, `GSRPHASE`, `GWDPHASE`, `RamCfg`.

## The JTAG opcode surface

`LatticeECP5.svp` defines **104 opcodes; Apollo knows 23.**

| opcode | what |
|---|---|
| `LSC_PROG_SPI` 0x3A | JTAG-to-SPI bridge — how every `flash-read`/`flash-program` works |
| `LSC_EBR_READ` 0xB0 / `LSC_EBR_WRITE` 0xB2 | block RAM over JTAG, no fabric involvement |
| `LSC_READ_TEMP` 0xE8 | die temperature, no `DTR` primitive |
| `LSC_READ_SED_CRC` 0xA4 | SED CRC directly |
| **`LSC_DEVICE_CTRL` 0x7D** | **arms device erase** |

**`LSC_DEVICE_CTRL` is a safety catch.** OpenOCD's sequence is `DEVICE_CTRL`
payload `8` → `DEVICE_CTRL` payload `0` → `ISC_ERASE`. It had been flagged as
"unexplored, worth sweeping" before that was found; it should not be probed
casually.

Two speculations were disproven while establishing this: `LSC_ISCAN` is
boundary scan sharing its opcode with `LSC_SAMPLE`, not a partial-reconfiguration
mechanism; and **`JUMP` does not appear in the ECP5 procedure at all**, so
runtime boot selection is weakly supported rather than demonstrated.

A methodological finding from the live-silicon probe worth keeping: **an opcode
issued without walking the TAP through RUN-TEST/IDLE looks identical to an
unsupported opcode.** Several apparent negatives were the harness, not the
device.

## What was specified and never executed

Stated plainly because these sections read like results and are not:

- **The fuzzer re-run differential.** Specified, not run.
- **The Diamond round-trip differ.** No Diamond was run and no bitstream
  generated. This is the obvious next step: two targets on a `PICL0` pad would
  confirm the `BASE_TYPE` diagnosis and let the correct encodings be read off
  directly, turning the report into a fix.
- **`-m`** is documented for ECP5 and unsupported by `ecppack`; what it emits
  is unestablished.

Two unexplained asymmetries also remain. ECP5 has 1459 mux/config collisions
against MachXO2's 18, but **zero** mux round-trip failures against MachXO2's
243 — opposite directions from a family-agnostic detector. And `MachXO` parses
to zero enums, muxes and words across all 71 tiles, which is structurally empty
rather than merely sparse.

## Where things live

| topic | home |
|---|---|
| ECP5 fuzzing, `BASE_TYPE`, SEDGA, corpus, Diamond family trap | `pluribus/docs/ecp5/` + pluribus #85-#91 |
| JTAG/USB configure speed, DMA, SCK, flash partitioning | `docs/luna_ecp5_fpga/` + #100 |
| Diamond mining scripts, opcode probe, DMA implementation | **unmerged worktree branches** (table above) |

The split follows the test "useful to someone with a different ECP5 board?" —
toolchain and bitstream-format work went to pluribus; programming-path and
board-specific work stayed here.

## What to do about the branches

Nothing here recommends merging all ten as-is — several are exploratory and
some overlap. But two are worth resolving before further firmware work, because
they conflict:

**`0xb8` is double-claimed.** It is `REQUEST_JTAG_GET_INFO` in `jtag.py` and
the synthetic benchmark also used it. The two apollo submodule commits have
diverged, and the flashed firmware has no `0xb8` at all.

The Diamond mining branch (3379 lines, 14 harvesters) is the largest single
body of otherwise-unreproducible work, since re-running it means re-mining a
12 GB vendor install.
