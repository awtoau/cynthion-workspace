# What Lattice Diamond 3.14 knows that the open ECP5 flow does not

Systematic sweep of the locally installed Diamond 3.14 (`~/lscc/diamond/3.14`,
12 GB) for ECP5 data absent from yosys / nextpnr-ecp5 / prjtrellis.

Harvesters live in `scripts/diamond_*.py`; structured output lands in
`tmp/diamond-mine/`, logs in `tmp/logs/`. Everything below was produced by
script, not by browsing.

## Device-tree attribution

Diamond splits device data by internal tree name. Getting this wrong has
already produced one false positive, so every finding below names its tree.

| tree | family |
|---|---|
| `ep5c00` | ECP5U / ECP5UM — **the Cynthion part (LFE5U-12F/25F)** |
| `ep5c00a` | ECP5-5G (LFE5UM5G) |
| `or5g00`, `mg5g00`, `xo2c00`, `sa5p00`, `se5c00`, … | other families — **not ECP5** |

`bitgen` itself accepts only three ECP5 architecture spellings: `ECP5U`,
`ECP5UM`, `ECP5UM5G`. It rejects the tree names (`bitgen -h ep5c00` errors).

## Correcting the record

Two open questions from earlier sessions are now settled — one confirming the
prior conclusion, one replacing it.

The general lesson is a source hierarchy. Diamond's documentation disagrees
with Diamond's binaries in several places, and the binaries win: `bitgen -h
<arch>` is generated from the tables the tool actually uses, whereas the
webhelp prose and the static `.usg` files are hand-maintained and stale (the
ECP5 `bitgen.usg` is dated **2008**; the tool is 2024). Where prjtrellis has
reverse-engineered the same thing from silicon, it has agreed with the binaries
every time it came up here.

**Ranking: `bitgen -h` / binary strings > prjtrellis bits > webhelp prose >
`.usg` files > `cae_library` simulation models.**

### ReadBack / ReadCapture are NOT ECP5 features — prior finding upheld, but for the wrong reason

The webhelp page `Reference Guides/Command Line/running_bit_generation_from_the_command_line.htm`
explicitly tags ReadCapture as applying to ECP5:

> `-g ReadCapture:<value>` … (ECP5, LatticeECP/EC and LatticeXP Only) Optional
> values are Disable (default) and Enable.

That text would overturn the earlier "not ECP5" conclusion. **It is wrong.**
`bitgen -h ECP5U` — the tool's own per-architecture table — lists exactly seven
`-g` options, and ReadBack/ReadCapture are not among them. The earlier
conclusion (reached from the `or5g00`/`mg5g00` tree evidence) was right; the
webhelp prose is unreliable and should not be cited on its own.

### The SEDGA `SED_CLK_FREQ` 77.5 / 155.0 puzzle is explained, and the correct set is now known

The previous session found `cae_library/simulation/verilog/ecp5u/SEDGA.v`
commenting values 77.5 and 155.0 that Diamond's own mapper rejects, and
concluded the simulation models are unreliable documentation.

Where those numbers came from: **77.5 and 155.0 are OSCG frequencies.**
`Reference Guides/FPGA Libraries/oscg.htm` gives the ECP5 oscillator table —
base 310 MHz, `DIV=2 → 155.0 MHz`, `DIV=4 → 77.5 MHz`. SED is clocked from
OSCG, so the model comment appears to have been copied from the oscillator's
top-end range rather than the SED block's own legal set.

The authoritative legal set comes from prjtrellis's bitstream database,
`tiledata/EFB2_PICB0/bits.db` (verified directly):

```
.config_enum SED.CLK_FREQ NONE
  2.4  4.8  9.7  19.4  38.8  62.0  NONE
```

So trellis and Diamond's mapper agree that 77.5 and 155.0 are fiction — **and
trellis additionally documents 62.0, which Diamond's sim-model comment omits
entirely.** The bitstream database is the better reference here. The lesson
generalises: the simulation models are not documentation in *either* direction,
they both overstate and understate.

Trellis also documents `SED.CHECKALWAYS` and `SED.SEDEXCLK_USED`.

## Authoritative ECP5 bitgen options (`bitgen -h ECP5U`)

Identical for `ECP5U`, `ECP5UM`, `ECP5UM5G`. First value is the default.

```
CfgMode     Disable, Flowthrough, Bypass
RamCfg      Reset, NoReset
DONEPHASE   T3, T2, T1, T0
GOEPHASE    T1, T3, T2
GSRPHASE    T2, T3, T1
GWEPHASE    T2, T3, T1
ES          Yes, No
```

Two things to note against the static `ep5c00/data/bitgen.usg`, which is **dated
2008** and disagrees with the live 2024 tool:

- **`GWEPHASE`**, not `GWDPHASE` as both the `.usg` and the webhelp prose spell
  it. `LatticeECP3` still says `GWDPHASE`, so this is a real ECP5-specific
  rename rather than a capture typo — and prjtrellis, reverse-engineered from
  silicon, independently uses `SYSCONFIG.GWEPHASE`. Anyone porting from the
  `.usg` would use the wrong name.
- **`ES` defaults to `Yes` on ECP5.** Live ECP5 help lists `ES Yes, No` (first
  is default); the `.usg` and the MachXO2/ECP3 tables list `No, Yes`.

There is also an **undocumented** ECP5 bitgen option: `-g DisableUES:FALSE`
appears on the real bitgen command line in the shipped ECP5 example build, but
in no usage text for any architecture.

None of these seven exist in `ecppack`. They control wake-up sequencing
(the order in which DONE, output enable, global set/reset and global write
disable are released), which is exactly the area governing whether a design
comes up cleanly after configuration.

## Findings ranked by what they would change

### 0. SEDGA works in the open flow up to the final step — two small patches away

Traced end to end, empirically, not by inspection:

1. yosys has **no** `SEDGA` in `share/yosys/ecp5/cells_bb.v` →
   `ERROR: Module '\SEDGA' ... is not part of the design`.
2. Adding a 9-line blackbox declaration (ports taken from Diamond's `SEDGA.v`)
   → yosys synthesises cleanly, emits `1 SEDGA` cell.
3. `nextpnr-ecp5` **already knows SEDGA as a real bel** — the chipdb has the bel
   and the full port list (`SEDSTDBY`, `SEDENABLE`, `SEDSTART`, `SEDFRCERR`,
   `SEDDONE`, `SEDINPROG`, `SEDERR`; confirmed present in the binary). It
   reports `SEDGA: 1/1 100%` and places and routes it successfully.
4. It then aborts in the bitstream writer:
   `Assertion failure: unsupported cell type (ecp5/bitstream.cc:1559)`.

So SEDGA needs a yosys blackbox declaration **and** a nextpnr `bitstream.cc`
case. Placement, routing and the underlying bit definitions all already exist.

This is listed first because it is the only finding here that is both
immediately actionable and directly useful to this project: for a board that
boots from flash, having the FPGA continuously self-check its own configuration
memory is a real robustness feature currently sitting behind a small patch.

Contrast with `PLLREFCS` and `IMIPI`, which are absent from nextpnr's chipdb
entirely — no bel, no string. Those are genuine silicon-support gaps, not
declaration gaps, and are much larger pieces of work.

### 1. Timing data: Diamond has ~2000× more of it, and the format is decoded

`ispfpga/ep5c00a/data/*.spd` (11.7 MB each, dated 2024). The format has been
parsed to **96.3%** coverage: named timing arcs, each with a cell-configuration
condition and four process corners, delays in picoseconds (raw units are
ps × 1024).

Extraction lands in `tmp/diamond-mine/spd/*.arcs.json` (22 MB each).

The five sections per file **are speed grades** — confirmed by checking that
delays rise monotonically across sections for arcs that should scale:

| arc | s0 | s1 | s2 | s3 | s4 |
|---|---|---|---|---|---|
| `CK_MPW` | 82.5 | 217.25 | 220.0 | 224.25 | 228.75 |
| `C2Q_DEL` | 280.0 | 800.0 | 800.0 | 870.0 | 940.0 |
| `PADI_DEL` | 62.5 | 101.0 | 101.0 | 106.5 | 112.25 |

Scale of the gap:

| | prjtrellis | Diamond `.spd` |
|---|---|---|
| entries | **33** cell timing entries | ~14,500 arcs per section |
| distinct arc names | — | 133 |
| corners | 3 | 4 |
| speed grades | 4 dirs (`speed_6/7/8/8_5G`) | 5 sections |
| vintage | 2025-02 files, older upstream data | 2024 vendor data |

**Caveat, stated plainly:** the parser recovers one record type only. The 133
arc names are I/O, logic, DQS and PLL arcs — `EBSR_CO` (the DP16KD block-RAM
arc named in the `.tac` schema) does **not** appear in any section, so EBR and
DSP arcs are in the unparsed 3.7% or in a record type not yet handled. The
extraction is substantial but not complete.

**`ispfpga/ep5c00a/data/ep5c00a.tac` is the schema that decodes it** — 98 KB of
**plain text**, 429 `CONF` blocks declaring every primitive configuration with
its ports, pin roles and named timing arcs, covering all hard blocks
(`DP16KD`, `MULT18X18*`, `ALU54A/B`, `DQSBUF*`, `SEDGA`, `LSLICE`, …). No
reverse engineering needed to read it.

This is the highest-value finding: timing errors are invisible until a design
mysteriously fails, and the open flow's timing model is thin by comparison.

### 2. Soft Error Injection — an entire workflow with no open equivalent

`bitgen` on ECP5 supports options absent from `ecppack` and undocumented in the
`.usg` file:

```
-sei <type>   Inject soft error in a bitstream frame.
              random: Pick a random bit
              unused: Pick a random bit in an unused site
-site <stype> When "-sei unused", select site type: PFU, EBR, DSP, ANY
```

`-sei` is confirmed present in the `bitgen` binary's strings and confirmed
**absent** from `ecppack`. `User Guides/Implementing the Design/Analyzing_Using_SEI.htm`
documents the SEI Editor GUI workflow and lists **ECP5U and ECP5UM** among
supported devices.

Combined with `-m <format>` (mask/readback file generation, ECP5-valid per both
the webhelp and the binary), this is a complete configuration-memory integrity
testing capability that the open flow cannot reproduce.

Note that SED *itself* is well supported in the open flow — `ecppack` knows
`SEDGA` and all its ports, nextpnr knows `SEDGA`, and trellis has the full SED
routing (`JSEDDONE_SED`, `JSEDERR_SED`, `JSEDINPROG_SED`, `SEDSTDBY_OSC` …) in
`tiledata/EFB0_PICB0/bits.db`. The gap is specifically the *injection and mask
file* tooling, not the primitive.

### 3. `-crc frame|global` — per-frame CRC, ECP5-valid, absent from ecppack

From the universal file writer (`ddtcmd`) reference:

> `-crc <frame|global>`: The "frame" option includes the CRC checking for each
> data frame. The "global" option disables the frames CRC but still calculates
> the global CRC at the end of the configuration data. **Valid for ECP5**…

`ecppack` has CRC16 insert/check machinery but exposes no frame-vs-global
control. Relevant to any work on bitstream robustness or on understanding
configuration failures.

### 4. OSCG divider table — 65 legal ratios the open flow does not validate

`Reference Guides/FPGA Libraries/oscg.htm` gives the full ECP5 table: base
310 MHz, DIV 2–128, but **non-contiguous above 32** (…32, 34, 36, 38, 40, 42,
44, 46, 48, 50, 52, 54, 56, 58, 60, 62, 64, 68, 72, 76, 80, 84, 88, 92, 96,
100, 104, 108, 112, 116, 120, 124, 128) — 65 legal values, each with its
typical frequency.

yosys declares `OSCG` with `parameter DIV = 128` and **no validation**.
prjtrellis encodes `OSC.DIV` as a 127-value enum in `tiledata/EFB0_PICB0/bits.db`,
but many of those values share identical bit patterns — i.e. the database maps
values the hardware cannot actually distinguish. Diamond's table tells you
which ones are real. Setting an unlisted DIV in the open flow silently gives
you a different frequency than you asked for.

The same 22-value frequency list appears in `ddtcmd` as the legal ECP5 MCCLK
set: 2.4, 3.2, 4.1, 4.8, 6.5, 8.2, 9.7, 12.9, 15.5, 16.3, 19.4, 20.7, 25.8,
31, 34.4, 38.8, 44.3, 51.7, 62, 77.5, 103.3, 155 MHz.

### 5. ECP5 primitives the open flow cannot instantiate

`Reference Guides/FPGA Libraries/ecp5u_um.htm` is the ECP5-specific primitive
list — better evidence than `cae_library/simulation/verilog/ecp5u/`, which
carries models Diamond's own mapper rejects. 143 primitives listed.

After excluding soft macros (gates, generic flip-flops, ROMs, muxes — yosys
infers these and needs no blackbox), and excluding I/O buffers (yosys maps
these from generic IO), the real gaps are:

| primitive | in nextpnr | in trellis | note |
|---|---|---|---|
| `PRADD18A`, `PRADD9A` | no | no | DSP pre-adders |
| `MULT9X9C`, `MULT9X9D`, `MULT18X18C` | no | no | DSP multiplier variants (yosys has only `MULT18X18D`) |
| `ALU24A`, `ALU24B`, `ALU54A` | no | no | DSP ALUs (yosys has only `ALU54B`) |
| `PLLREFCS` | no bel | routing only | PLL dynamic reference clock switching — see note below |
| `IMIPI` | no | no | MIPI input support |
| `BCINRD`, `BCLVDSOB`, `INRDB` | no | no | dynamic bank controllers |
| `START` | no | yes | startup controller |

On `PLLREFCS` specifically, be careful not to overstate what trellis has.
`tiledata/PLL0_*/bits.db` contains PLLREFCS **routing** entries
(`.fixed_conn N1_CLK0_PLLREFCS N1_REFCLK0`, `N1_JSEL_PLLREFCS`, …), so the mux
into the PLL is mapped — but nextpnr's chipdb has **no PLLREFCS bel**. It is
therefore not merely a missing declaration like SEDGA; it needs bel definition
work as well. Closer than a from-scratch primitive, further than SEDGA.

The DSP gaps matter for anyone wanting the full sysDSP feature set; the open
flow supports one multiplier (`MULT18X18D`) and one ALU shape (`ALU54B`) out of
several.

**Where yosys is not behind — a negative result worth recording.** For every
ECP5 hard block yosys *does* declare, its parameter set matches Diamond's
synthesis model exactly, including `DCUA` at all **265** parameters, plus
`JTAGG`, `EXTREFB`, `PCSCLKDIV`, `DTR` and `EHXPLLL`. There is no
parameter-richness gap to close on the SERDES or the PLL. Two apparent extras
(`EHXPLLL.FIN`, `DDRDLLA.LOCK_CYC`) exist only in the *simulation* model and
not the synthesis model — testbench annotations with no silicon bits. Not worth
chasing.

Also note `ecp5u`, `ecp5um` and `ecp5um5g` synthesis models are **byte-identical**
(same md5). ECP5 has one primitive library; the -UM/-5G difference is
device-level (SERDES presence and rate), not cell-level.

### 6. sysCONFIG: 9 ECP5 attributes Diamond sets have no bits in prjtrellis

Diamond's ECP5 sysCONFIG set (cross-checked three ways: `ep5c00/data/edif2ngd.prp`,
the `SYSCONFIG.htm` rows tagged ECP5, and a real LFE5U-25F build under
`examples/Reveal_debugger/counter_reveal_ECP5/`) is 17 attributes.

Every `SYSCONFIG.*` key in the entire prjtrellis ECP5 database is 11 (verified
by grep over `database/ECP5/tiledata/*/bits.db`):

```
BACKGROUND_RECONFIG  DONE_EX  DONEPHASE  GOEPHASE  GSRPHASE  GWEPHASE
MASTER_SPI_PORT  SLAVE_PARALLEL_PORT  SLAVE_SPI_PORT  TRANSFR  WAKE_UP
```

**In Diamond for ECP5, no bits in trellis:** `MCCLK_FREQ`, `CONFIG_SECURE`,
`COMPRESS_CONFIG`, `DONE_OD`, `DONE_PULL`, `CONFIG_IOVOLTAGE`, `CONFIG_MODE`,
`PERSISTENT`, `INBUF`.

Two deserve singling out because they look supported but are not:

- **`MCCLK_FREQ`** — the string appears in the `ecppack` binary and `--freq`
  exists, but there are **zero MCCLK bits anywhere in the ECP5 trellis
  database**. Worth verifying what `ecppack --freq` actually encodes.
- **`CONFIG_SECURE`** — readback lockout. Diamond: when ON, no readback is
  supported through the sysCONFIG or ispJTAG port. No trellis bits, so an
  open-flow ECP5 bitstream cannot set the readback-disable bit at all.

Note this list independently corroborates `GWEPHASE` over the webhelp's
`GWDPHASE` — trellis, reverse-engineered from silicon, uses the same spelling
the binary does.

Separately, nextpnr *does* accept 15 SYSCONFIG keys on its input (it parses
`SYSCONFIG <attr>=<value>`), so the front-end is not the bottleneck; the missing
bit definitions are.

### 7. `bstool` — vendor bitstream disassembler and BFD dumper

`ispfpga/bin/lin64/bstool` runs once given Diamond's environment (it needs
`LD_LIBRARY_PATH` to find `libbasbs.so`). Confirmed working; usage captured:

```
-x <arch> <f1> <f2>    Convert <arch> bitfile to NeoCAD format.
-c/-r <file1> <file2>  Compare two NeoCAD bitfiles (binary / raw ASCII).
-d <bitfile>           Dump a NeoCAD bitfile.
-i <bitfile>           Print info about a NeoCAD bitfile.
-a                     Write an ascii BFD file (must precede -b)
-b <arch> <asc> <bin>  Write a binary BFD file.
-s <bitfile> {<soifile>}  Print a soisim file.   -l  create location file
-t <bitfile>           Test the BFD against a bitfile
```

The **BFD is the bitstream frame database** — the tile-to-bit mapping that
prjtrellis reverse-engineered by experiment. `bstool -a`/`-b` reads and writes
it in ASCII. For anyone extending the trellis ECP5 database this is a direct
route to vendor ground truth. I confirmed the tool runs and prints its usage
but did **not** succeed in producing a BFD dump — the `-a`/`-b` argument order
needs working out. That is the single highest-value loose end left.

### 8. ECP5 cannot encrypt its primary bitstream

`ddtmain` contains the literal string **"ECP5 does not support the encrypted
primary."** This is a hard silicon/tooling constraint rather than an open-flow
gap, and it bounds what any ECP5 secure-boot design can do. Consistent with
`bitgen -h ECP5U` omitting `-e`/`-s`/`-k`. Caveat: a real ECP5 `.bgn` in the
examples tree does show `-e -s -k` on the bitgen command line, so the flags are
accepted for ECP5 — the restriction is specifically on the *primary* image.

### 6. Package pinout and geometry data is decodable

The `.pkg`, `.hrg`, `.grf`, `.nph`, `.bxg`, `.tld` files are **zlib-compressed**
and inflate to a self-describing tagged format with plain-text headers
(`Format Version: 9.1`, creation dates, device names) and readable site/port
name tables (`APIO`, `IOLOGIC`, `IOLDO`, `ECLKDQSR`, `DDRCLKPOL`, …).

Package pinouts were successfully recovered — e.g. for `ep5c00a`, packages
`FPBGA256/484/672/1152/1156`, `FTBGA256`, `TQFP144` with 1452–1748 ball records
each. Relevant to the ECP5 lifter work: this is vendor geometry data in a
tractable format, not an opaque blob.

## What was examined

- **`docs/webhelp/eng/`** — all 2092 HTML pages harvested to JSONL with text and
  2105 extracted tables. Fully swept.
- **`bin/lin64/` and `ispfpga/bin/lin64/`** — CLI tools run with a correctly
  reconstructed Diamond environment; `bitgen` per-architecture help captured
  for every architecture it admits to.
- **`.usg` usage files** — harvested across all device trees, attributed.
- **`ispfpga/ep5c00*/data/`** — every file classified text/binary; `.spd` parsed
  to 96.3%; `.pkg`/geometry files inflated and characterised; `.tac` read.
- **`cae_library/`** — ECP5 sim (157) and synthesis (146) models diffed against
  yosys, including a parameter-level three-way classification against trellis.
- **`examples/`** — all 13 projects inventoried, primitive instantiations and
  constraints extracted. Result is thin: only **one** targets ECP5
  (`SimpleDesign_ECP5U`, LFE5U-45F), a trivial 4-bit adder with **zero**
  primitive instantiations. The other 12 target MachXO2/ECP2/ECP3/XP2/SC and
  their primitives do not transfer. The example tree's only ECP5 value was the
  `.sty` bitgen property list and the real `.bgn`/`.prf` command lines.
- **`micosystem/` (629 MB)** — inventoried and sampled. Largely MachXO2/Platform
  Manager oriented; `dualboot/` and `ascboot/` are built on the hard EFB that
  ECP5 does not have. There is **no USB IP** anywhere in it. One item of
  architectural interest: `components/sefb/` (Soft EFB), Lattice's own soft
  I2C+SPI replacement for the hard EFB, Wishbone-attached with tri-state
  break-outs designed to arbitrate SPI access against another master — a
  directly comparable problem to sharing SPI flash between a SoC and the
  configuration engine. Note its licence header is proprietary; read for
  architecture, do not copy.
- **`share/trellis/database/ECP5/`, yosys `cells_bb.v`, `ecppack`/`nextpnr-ecp5`
  strings** — used as the cross-reference baseline throughout.

## What was not examined

- **`.bfd` (80 MB)** and the bulk of `.hrg`/`.nph` — format characterised
  (zlib-compressed, self-describing) but contents not extracted. This is where
  the full routing graph lives, and `bstool -a` may be a shortcut into it.
- **The unparsed 3.7% of `.spd`**, which is where the missing EBR/DSP timing
  arcs most likely are.
- **`ep5c00`'s own `.spd`** — every file parsed was `ep5c00a` (ECP5-5G).
  `ep5c00` is the Cynthion tree; its timing files must be parsed to confirm the
  format matches before any of the timing data is trusted for this board.
- **`questasim/`, `synpbase/`, `module/`** — third-party simulator and Synplify
  install trees, judged out of scope.
- **`ddtcmd`/`ddtcmain`** — menu-interactive, yields no useful non-interactive
  usage. Its capabilities were inferred from `ddtmain` strings only, so the
  multiboot/golden-image and CRC/ACA-compression items from that binary are
  **leads, not confirmed ECP5 capabilities** — `ddtmain` is family-generic.

## Where the next pass should start

1. **`bstool -a`/`-b` argument order.** It runs; getting a BFD ASCII dump out of
   it would give vendor ground truth for the tile-bit database. Highest value
   per unit effort of anything left.
2. **Parse `ep5c00`'s `.spd`** (the actual Cynthion device tree), not `ep5c00a`.
3. Find the record type holding EBR/DSP timing arcs, using `.tac` arc names such
   as `EBSR_CO` as the search target.
4. Compare recovered per-arc delays against
   `share/trellis/database/ECP5/timing/` entry by entry — any systematic
   disagreement is a real correctness bug in open-flow timing.
5. Verify what `ecppack --freq` encodes, given there are no MCCLK bits in the
   ECP5 trellis database.
6. SEDGA: yosys blackbox + nextpnr `bitstream.cc` case (see finding 0).
