# ECP5 `LFE5U-12F` — the FPGA, and it is a 25F die

The main programmable device on Cynthion r1.4. Lattice ECP5, marked `LFE5U-12F`,
CABGA256, speed grade 8.

**Index:** [`../hardware.md`](../../hardware.md)

## The headline: the part marked 12F is a 25F die, and the extra fabric computes

| what | value | source |
|---|---|---|
| IDCODE | `0x21111043` | `apollo jtag-scan`; also read back out of the bitstream by `ecpunpack` |
| part reported | `LFE5U-12F` | same |
| LUT4s advertised for a 12F | 12,288 | datasheet |
| LUT4s on the die | 24,288 | a 25F; what `nextpnr-ecp5 --12k` reports |
| **LUT4s placed, routed and verified** | **20,143** (82.9%) | [#116](https://github.com/awtoau/cynthion-workspace/issues/116) |
| beyond the marking | **7,855 LUT4s** | same |
| timing | 86.43 MHz achieved against a 60 MHz constraint | nextpnr |
| correctness | **22,026 rounds, zero mismatches** (2,002 + 20,024 across two runs) | fabric test |
| negative control | 1,575 of 1,575 rounds mismatched, sticky flag set on all 200 reads | `--golden 0xdeadbeef` build |

**No patching is involved.** `ecppack` writes the genuine 12F IDCODE; nextpnr's
chipdb is per-die and already knows about all 24,288 LUTs. The vendor's own files
say the dies are the same: byte-identical `.con` package files, identical
`frames × bits_per_frame`, byte-identical Trellis `tilegrid.json`, IDCODEs
differing only in the top nibble.

**Why the control matters.** A self-checking test that never reports a failure is
indistinguishable from one that cannot fail. The `0xdeadbeef` build proves the
detector fires, so zero mismatches is a real negative rather than a broken test.

**Why the timing matters.** 12F and 25F share a speed grade, so 86.43 MHz against
a 60 MHz constraint is genuine margin, not a design that only closed because the
extra fabric was clocked gently.

**Where the logic landed.** `fabric_placement.py` parses `top.config` — the
placement as committed — and finds logic in **44 of 47 tile rows** (R2–R48; the
three empty rows are EBR/DSP rows on this die, not holes) across 69 columns,
flatness 0.73. The design could not have confined itself to a 12k-sized subset.

### What this does *not* establish

**Intermittent defects.** This is one part and a single load-and-check, not a
soak. Binning for occasional wrongness is not excluded by a passing run. Treat
the extra fabric as usable, not as guaranteed across parts.

## Block RAM

nextpnr reports **56 DP16KD** — the 25F figure, not the 12F's 28. Every SoC build
places 41 of them, carrying the CPU's I-cache, D-cache, 64 KiB of program memory
and the console FIFO.

**Block RAM has not been walked** the way the fabric was. It is nonetheless taken
as working on the strength of ordinary use: the CPU fetches from block RAM,
executes and computes correctly while the FIFO carries characters uncorrupted.
Marginal block RAM surfaces as garbage instructions or dropped bytes, not as
something subtle. Worth revisiting only if a fault appears that smells like memory
corruption.

Who actually consumes it: [`../chips/ecp5/bram-budget.md`](../ecp5/bram-budget.md).

## DSP blocks

The ECP5 has DSP blocks, so a hardware multiplier is cheap. Measured at **16
cycles** for an integer multiply against 123 for a soft-float single-precision
multiply — which is why `rv32im` earns its area on this part. CPU configuration
is in [`vexiiriscv-cpu.md`](../vexiiriscv-cpu.md).

## Clocking

One discrete **60 MHz oscillator on pin A8**. Everything else comes from the PLL.

The PLL is driven by `VariableClockDomainGenerator`
(`repos/apollo/apollo_fpga/gateware/variable_clock.py`), not upstream's
`LunaECP5DomainGenerator`, because upstream offers only 60/120/240 MHz from
hardcoded taps. Ours solves for `sync` **and** `usb` together so `usb` lands on
exactly 60 MHz — `ecppll` optimises its primary output and lets the secondary fall
where it may, and a `usb` clock 3.7% out does not enumerate the ULPI PHY. See
[`../upstream-boundary.md`](../../upstream-boundary.md) and #111.

How fast the soft CPU can be clocked on this part:
[`../soc-clocking.md`](../../soc-clocking.md).

## How software reaches it

| path | mechanism |
|---|---|
| configuration over JTAG | Apollo bit-bangs the TAP; `apollo` CLI |
| configuration from flash | at power-on, from the [W25Q32](../w25q32-config-flash.md) at offset 0 |
| debug registers / ILA | JTAG ER1 (`0x32`) / ER2 (`0x38`) tunnel, via `JTAGRegisterInterface` |
| reconfigure | Apollo drives PROGRAMN (MCU PA08); the fabric can also self-trigger via `self_program` (T13) |

JTAG pins on the FPGA side are **R11 (TDI)** and **T11 (TMS)**, which are wired to
the UART pins R14/T14 — see the pin-sharing section of
[`../hardware.md`](../../hardware.md).

## Registers

**SoC peripheral registers are not documented here.** The SoC's own memory map is
the authority — see [Register reference](../../hardware.md#register-reference) in the
board index. This note covers the silicon, not the gateware running on it.

## Code and scripts

| | |
|---|---|
| pin map (vendored) | `ecp5-test/cynthion_platform/cynthion_r1_4.py` |
| fabric test gateware | `ecp5-test/fabric/fabric_gateware.py` |
| build / run / control | `scripts/fabric_build.py`, `fabric_run.py`, `fabric_negative_control.py`, `fabric_placement.py`, `fabric_sim.py`, `fabric_golden.py` |
| flashing and configuration | [`../chips/ecp5/flashing.md`](../ecp5/flashing.md) |
| live opcode sweep | [`../chips/ecp5/config-engine-probe.md`](../ecp5/config-engine-probe.md), `scripts/ecp5_cmd_probe.py` |

Generic ECP5/toolchain findings live in pluribus (`docs/ecp5/`), not here — the
test is whether a finding would be useful to someone with a different ECP5 board.

## Getting DDR data in and out at speed

Moved here from `memory-speed-options.md`, because these are ECP5 primitives and
the question gets asked about the FPGA, not about the memory. The HyperRAM side
of the same problem is in [`w956a8-hyperram.md`](../w956a8-hyperram.md); what other
projects achieved with these primitives is below and in the part doc.

### The clock structure is not the canonical ECP5 one, and that is the leading hypothesis

`hyperram_ceiling_top.py` takes `sync` from **`CLKOP`** and `fast` from
**`CLKOS2`** — two independent PLL outputs at a 2:1 ratio, each with its own
clock buffer. `CLKDIVF` usage in every built design is **0 of 4**.

The Lattice-canonical structure for a `DQSBUFM` interface is
**PLL → ECLK → `CLKDIVF` (÷2) → SCLK**, with `ECLKSYNCB` in the path. That
matters for two reasons:

- With `CLKDIVF`, SCLK is *derived from* ECLK, so their phase relationship is
  structural. With two PLL outputs it is only nominal — the PLL guarantees
  frequency, and each output has its own buffer and its own insertion delay.
- **`CLKDIVF` has an `ALIGNWD` port whose entire purpose is to slip the divided
  clock by one fast-clock cycle.** That is the standard mechanism for correcting
  exactly the fault we see: a word-boundary slip in 4:1 gearing, where the data
  is right but arrives in the wrong half.

Our CK 200 failure — *"the two 16-bit halves are transposed and one of them
belongs to a neighbouring word"* — is a textbook description of what `ALIGNWD`
exists to fix, and this design has no `ALIGNWD` because it has no `CLKDIVF`.

**There is no `ECLKSYNCB` either**, in ours or upstream's. The PLL's `CLKOS2`
goes straight to `ClockSignal("fast")` and nextpnr promotes it to an edge clock
by itself. `ECLKSYNCB` exists to gate ECLK so it *starts* in a known
relationship to SCLK; without it, the phase at which the two domains come out of
reset is whatever the PLL and the routing happen to give on that
place-and-route. Both `CPHASE` values are set to `div - 1` — the
no-shift convention — which makes them nominally aligned and specifies nothing
about the alignment that actually results.

**Upstream has the same structure**, so this is not a local mistake:
`repos/luna/luna/gateware/architecture/car.py` takes 240/120/60 MHz from
`CLKOP`/`CLKOS`/`CLKOS2` with hand-set `CPHASE` values of 1/3/7 and no
`CLKDIVF`. Upstream at least tuned those phases; ours are formulaic. **That is
the cheapest experiment in this section** — sweeping `CLKOS2_CPHASE` and
`CLKOS2_FPHASE` at CK 200 costs bitstreams and no design change, and if the
failure moves with phase it is confirmed as an alignment fault before anyone
commits to the `CLKDIVF` restructure.

**Cost:** restructuring the clock domain generator, which is not small, and it
changes every HyperRAM bitstream. **Value:** it is the only candidate on this
page that addresses the failure's actual signature rather than its
circumstances.

### `READCLKSEL` — and it does not do what this workspace assumed

`psram.py:779-782` hardcodes `READCLKSEL0=0, READCLKSEL1=1, READCLKSEL2=0` with
upstream's *"TODO: may need to tune at runtime by trying different values &
checking for BURSTDET high"*. It never was swept — but before anyone sweeps it,
the semantics matter, and they are not what "read clock select" suggests.

**Lattice FPGA-TN-02035-1.3, "ECP5 and ECP5-5G High-Speed I/O Interface", §6.2.4**
([mirror](https://0x04.net/~mwk/doc/lattice/ecp5/FPGA-TN-02035-1-3-ECP5-ECP5-5G-HighSpeed-IO-Interface.pdf)):

> Once READ1/0 positions the READ pulse, READCLKSEL2/1/0 can be used to **shift
> the READ pulse by 1/4T per step**. With the total eight possible combinations
> from 000 to 111, READCLKSEL2/1/0 covers the READ pulse shift up to a **whole 2T
> timing window**. If BURSTDET is asserted with a certain READCLKSEL2/1/0 value,
> it indicates that the READ pulse has been located to the optimal position. **If
> no BURSTDET is asserted during this step, the READ pulse needs to be moved to
> the next timing window.**

So `READCLKSEL` positions the **READ gating pulse**, not a phase-shifted capture
clock. The 90° shift comes from `DDRDLL`/`DDRDEL` into `DQSR90`; the fine delay
is a third mechanism (`RDMOVE`). Three separate knobs, and this workspace had
conflated the first two.

**Three consequences for the sweep:**

1. **Eight values span only 2T.** If the round-trip DQS delay lands outside that
   window, no `READCLKSEL` value works, and the fix is to move `READ1`/`READ0` to
   the next cycle — an *outer* loop the existing harness has no notion of. The
   same section warns that **moving `READ1` alone gives "two short pulses in wrong
   timing"**; both must move together.
2. **`PAUSE` is mandatory around the change**, not optional: *"the PAUSE input to
   DQSBUFM must be asserted before 4T of the change and remain asserted for
   another 4T after the change"*. LiteX hit this the hard way — litedram#103, two
   of six OrangeCrabs failing memtest after a successful read-levelling, fixed by
   strobing `PAUSE` afterwards.
3. **The ECP5 datasheet's port table is wrong.** It lists `READCLKSEL[1:0]` — two
   bits. The technical note, the primitive and prjtrellis all have three.

**Correction: the port table discrepancy is a datasheet error, not a silicon
limit.** An earlier draft of this page treated it as "8 values on paper, possibly
4 in silicon". TN-02035 settles it at eight.

### Tiliqua has already implemented LUNA's TODO, and it is a drop-in

`apfaudio/tiliqua` vendors LUNA's `psram.py` split across three files and changed
**exactly one thing that matters**. Where LUNA hardcodes `READCLKSEL = 0b010`,
Tiliqua drives it from a runtime register with the mandatory `PAUSE`-before /
`PAUSE`-after sequence, and runs a training FSM (`periph/psram.py:198-223`):

    with m.If(timeout == 127):
        m.d.sync += counter.eq(counter + 1)
        with m.If(counter == 127):
            m.next = "IDLE"
        with m.If(~psram.phy.burstdet):
            m.d.sync += readclksel.eq(readclksel + 1)
            m.d.sync += counter.eq(0)

Dummy read, wait, check `BURSTDET`; if low, increment `READCLKSEL` (wrapping
0→7) and restart. It requires **128 consecutive bursts with `BURSTDET` high**
before releasing — matching TN-02035's recommendation exactly. Commit
`37180a74`, September 2024.

**This is the single most reusable thing found.** Same file lineage, same
primitive, proven on real ECP5 HyperRAM silicon — and it establishes that
`BURSTDET` *does* assert on a HyperBus part, which was the open question.

Two caveats before copying it wholesale:

- **Tiliqua runs at CK 120 MHz**, 40% below where we already are, so it is not
  evidence about 192 or 200.
- **First-pass-wins is the wrong policy.** It stops at the first `READCLKSEL`
  that works, which may be the edge of the eye. `jeanthom/gram`
  (`libgram/src/calibration.c`) does it properly: sweep 0..7, find the **minimum**
  and **maximum** values that assert, and program the **midpoint**. LiteX's BIOS
  does the same with `delay_mid = (delay_min + delay_max) / 2` and a comment
  worth keeping — `delay_min = delay - 1; // delay on edges can be spotty`.

Everything else in Tiliqua is unchanged from LUNA, including
`LOW/HIGH_LATENCY_CLOCKS = 3/5`, the `extra_latency | 1`, and the tied-off margin
control. **Neither LUNA nor Tiliqua writes CR0 at all**, so every option in the

### `BURSTDET` — four specific reasons ours may be staying low

TN-02035 §6.2.4 imposes conditions this design does not obviously meet:

> **A minimum burst length of eight on the memory bus must be used in the training
> process.** … The BURSTDET signal is asserted **after the last DQS transition is
> completed** … the memory controller should **wait until the DATAVALID signal
> from DQSBUFM is asserted and then sample the BURSTDET signal at the next
> cycle**. … It is recommended that **at least 128 read operations** be performed
> repetitively at a READ pulse position.

Table 6.3, for X2 gearing: initial `READ` assertion **"at least 5.5T before
preamble"**, `READ` width **"Total Burst Length / 4"** SCLK cycles.

Against that, in likely order:

1. **`READ0`/`READ1` windowing.** LUNA drives them straight from `phy.read`,
   raised in `READ_DATA`. If that is not ≥5.5T before the strobe and held for
   burst/4 SCLK cycles, `BURSTDET` cannot assert **at any `READCLKSEL` value** —
   which would make a sweep of the inner loop alone come back empty and prove
   nothing.
2. **Sampling method.** `burstdet_seen` is latched from a level. gram's commit
   `0cd69420` is titled *"Detect burstdet on rising edge, not by logic level"*,
   and exists because level-sampling misleads.
3. **`READCLKSEL` fixed at one point in a 2T span**, per above.
4. **RWDS is not DQS.** `DQSBUFM`'s burst detector is built for DDR3's
   preamble/postamble, which HyperBus does not have. Tiliqua's simulation path
   force-ties `burstdet.eq(1)` for want of a model — **but Tiliqua gets real
   assertions on hardware at CK 120**, so it is achievable on HyperRAM and this
   is the least likely of the four.

### `RDLOADN` / `RDMOVE` — tied off, and that is correct

`psram.py` sets `RDLOADN=0, RDMOVE=0, RDDIRECTION=1`. An earlier draft of this
page listed sweeping them as a cheap experiment. **That was wrong**, and the
correction is worth recording because it is the opposite of what the port names
suggest.

TN-02035 §8.10.1: *"If margin control is not used, then LOADN should be low to
**continuously get code from DDRDLL**."* `RDLOADN=0` is the setting that keeps the
delay line tracking process, voltage and temperature automatically. Raising it
hands **you** responsibility for tracking PVT via `DCNTL[7:0]`. LiteDRAM ties them
off identically.

So this is a real knob, but it is not free and it is not first: it converts an
automatic delay into a manual one. Do it only after `BURSTDET` works, when there
is something to centre against.

### The ECP5 is not the limit on paper — but nothing on paper covers this bus

Speed grade **8**, the fastest ECP5 bin. From the family datasheet:

| spec | -8 | our worst case |
|---|---|---|
| `fMAX_GDDRX2` (ECLK), generic DDR | 400 MHz | 200 MHz at CK 200 |
| `fMAX_DDR3` (ECLK) | 400 MHz | 200 MHz at CK 200 |
| `tDWDQ`, DQ input valid window required | 0.519 ns | part gives 1.45 ns of `tDV` |

**Twice the headroom on ECLK.** The ECP5's published DDR limits do not explain a
failure at CK 200.

**The eye comparison is not as comfortable as it looks**, though, and the first
draft of this page overstated it. `tDV` is data valid relative to *CK*;
`tDWDQ` is the window the FPGA needs relative to *DQS*. Closing the gap between
them costs `tDSS`/`tDSH` — **±0.8 ns on our bin** — so the figure that accounts for it is
1.45 − 2 × 0.8 = **−0.15 ns** in the worst case, i.e. the strobe-relative eye is
not guaranteed open at all at CK 200 on a 166 MHz part. The CK 192 result that
this paragraph once cited as evidence the part sits inside its guardband is
**withdrawn** — CK 180 fails in bulk. Nothing here says there is margin.

**Except that none of those numbers were characterised on our I/O standard.** The
datasheet's own notes: *"Generic DDR timing numbers based on LVDS I/O"*, *"DDR3
timing numbers based on SSTL15"*, *"General I/O timing numbers based on
LVCMOS 2.5, 12 mA, Fast Slew Rate, 0 pF load"*. **This bus is LVCMOS33** —
a higher-swing, higher-capacitance buffer that appears in none of them.

So the correct statement is not "the FPGA has margin" but **"no vendor number
covers 400 Mbps/pin DDR on LVCMOS33, in either direction"**. It is neither
endorsed nor excluded. Our 384 Mbps/pin at CK 192 is unpublished territory and
the measurement is the only authority.

### `ALIGNWD` is the published fix for our exact failure, and there is firmware to port

Lattice FPGA-TN-02200-1.3, the sysCLOCK PLL/DLL guide, Table 13.1:

> **ALIGNWD** | I | Signal is used for word alignment. **When enabled it slips the
> output one cycle relative to the input clock.** The ALIGNWD input is intended
> for use with high-speed data interfaces such as DDR or 7:1 LVDS Video.

The ecosystem splits two ways, and the split is informative:

- **LiteDRAM ties `ALIGNWD = 0` everywhere** and does word alignment in fabric
  with a soft `BitSlip(4)` on a CSR — deliberately separate from the
  `READCLKSEL` read delay.
- **Every Davill-lineage ECP5 HyperRAM controller drives `CLKDIVF.ALIGNWD` from a
  CSR.** `orbtrace/orbtrace/crg_ecp5.py:41-59`, and identically in `boson-sd` and
  `DiVA`:

      self._slip_hr2x   = CSRStorage()
      self._slip_hr2x90 = CSRStorage()
      ...
      i_ALIGNWD = self._slip_hr2x.storage,

**The firmware is the part to steal** —
`boson-sd/firmware/main_fw_bootstrap/hyperram.c:80-113`, two nested loops:

- **outer:** `clk_del` 0..3, the two `ALIGNWD` bits giving four coarse
  word-boundary positions, applied as **one-shot pulses, not levels**;
- **inner:** sweep the PLL's dynamic phase steps, memtest at each, look for a
  passing window of ≥5–6 steps, then centre by stepping up `window/2`;
- if no window at this slip, bump `clk_del` and slip again. It prints a
  pass/fail map as it goes.

That is Lattice's own documented 7:1-LVDS word-alignment pattern (TN-02035
§9.1.4: *"Each pulse on ALIGNWD rotates the 7-bit bus by 2 bits"*) applied to
HyperRAM. **It is the only published open-source fix for a word-boundary slip on
ECP5**, and its shape — a coarse word slip crossed with a fine phase sweep — is
what our CK 200 failure calls for.

**One gotcha:** attach `ALIGNWD` to **`CLKDIVF`**, not `IDDRX2F`. Glasgow leaves
`IDDRX2F.ALIGNWD` unconnected with a comment pointing at nextpnr#1749; `CLKDIVF`
is the well-trodden path. This is a second, independent reason the
`CLKDIVF` restructure is the right move — it is not only the canonical clock
structure, it is where the fix attaches.

**Probable root cause, from the same survey:** `ECLKSYNCB.STOP` → `CLKDIVF`/IDDR
reset skew gives a *random word-boundary phase at power-up*. Lattice's own answer
is the GDDRX_SYNC soft IP for deterministic ECLK restart, which TN-02035 requires
for every GDDRX2 interface. None of the open HyperRAM designs use it — which is
precisely why they all need a firmware slip sweep instead.

### There is no `USRMCLK` maximum to violate

This is the cleanest result of the flash survey, and it retires a number this
workspace has been quoting.

- **The string `USRMCLK` does not appear in the ECP5 datasheet.** The 62 MHz
  figure is `fCCLK`, *"max selected CCLK output frequency"*, in the sysCONFIG
  port timing table — the **configuration engine's** oscillator ceiling. Applying
  it to the user-mode mux path is an extrapolation, not a spec.
- **FPGA-TN-02039 §6.1.2 is the only `USRMCLK` documentation**, and it gives
  instantiation templates and three functional notes — **no fmax, no setup/hold,
  no skew, no jitter**.
- **The open toolchain models nothing either.** The bel has three pins and three
  fixed connections; `getCellDelay` has no `USRMCLK` case; there is **no
  CCLK/USRMCLK/MCLK entry in any speed grade** of prjtrellis's timing database.
  A clean nextpnr report says nothing whatever about this path.

So [`chips/w25q32-config-flash.md`](../w25q32-config-flash.md)'s *"132% past
the 62 MHz Lattice specifies for `MCLK`"* is comparing against the wrong number.
**There is no vendor figure for user-mode `USRMCLK` at all** — we are in
unmodelled territory, and measurement is the only authority. That is a stronger
statement than the one it replaces, not a weaker one.

### `ODDR` into `USRMCLK` is architecturally impossible, not merely refused

The existing note records that nextpnr refuses an `ODDRX1F` whose `Q` does not
drive a top-level output. That is true, and it is the lesser reason:

- nextpnr's check is `is_trellis_io`, i.e. `cell->type == id_TRELLIS_IO`;
  `USRMCLK` is a different cell type, so the packer hard-errors.
- **But the CCLK site has no `DATAMUX_ODDR`/`IOLDO` mux** in the Trellis routing
  database, unlike every real PIO — and `JA4`'s mux sources carry **no
  `G_HPBX` global-clock spine source**, so a global clock cannot reach
  `USRMCLKI` without passing through a LUT or FF.

**There is no software fix.** The two things people actually do are
hand-placed fabric DDR (`dan-rodrigues/icestation-32` places two `TRELLIS_SLICE`
FFs on opposite edges next to the CCLK site) — which reaches the fabric rate and
does not exceed it — or NanoMig's phase-shifted PLL straight into `USRMCLKI`.
**We are already at 144 MHz, past both.**
