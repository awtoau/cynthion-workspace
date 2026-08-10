# The FPGA's own pin attributes

`DRIVE`, `SLEWRATE`, `PULLMODE`, `HYSTERESIS` on the HyperRAM pads. [#311](https://github.com/awtoau/cynthion-workspace/issues/311).

Cost: **a patch and a reconfigure, ~3 s each**, against 3-8 minutes for a
bitstream. Nothing else in the matrix is this cheap. What it is not is
*measurable* — see [Neither operating point can resolve it](#neither-operating-point-can-resolve-it).

## What the built bitstream actually says

`./scripts/hyperram_pin_patch.py --show`, read out of `top.config` — the board
file names none of it.

| group | pins | DRIVE | PULLMODE | SLEWRATE | HYSTERESIS |
|---|---|---|---|---|---|
| `clk` CK | C3 / PIOC | unset | NONE | **FAST** | — |
| `clk` CK# | PIOD, unnamed in the LPF | unset | NONE | **unset = SLOW** | — |
| `cs` | B2 | unset | NONE | FAST | — |
| `dq[7:0]` | F2 B1 C2 E1 E3 E2 F3 G4 | unset | NONE | FAST | **ON** |
| `rwds` | D1 | unset | NONE | FAST | **ON** |
| `reset` | C1 | unset | NONE | FAST | — |

- **CK# really is at SLOW.** Amaranth emits an LPF entry for `port.p` only, so
  nextpnr writes `SLEWRATE` to the named PIO and never to the differential
  partner. Confirmed in the bitstream, not inferred.
- **HYSTERESIS is ON across all of DQ and RWDS**, and nobody chose it — it is
  part of Trellis's `BIDIR_LVCMOS33` encoding.

## The default DRIVE is 8 mA

- `.config` says `DRIVE=None`: nextpnr never wrote the attribute.
- The BITS say 8. `BIDIR_LVCMOS33` sets `F0B4 F8B3 F9B3` and clears `F1B4 F2B4`
  — exactly `.config_enum PIOB.DRIVE 8`. The CK pad likewise: PIOC's
  `OUTPUT_LVCMOS33D` sets `F2B6 F3B6 F4B6`, clears `F5B6 F6B6` = `DRIVE 8`.
- #311's open question 2 is answered from the database, not assumed.

## The four attributes share config bits with BASE_TYPE

They are not independent fields, and the collision is invisible in the `.config`
— symbolic there, merged only once packed.

- `HYSTERESIS ON` is `F7B3`, which `BIDIR_LVCMOS33` also sets. Turning hysteresis
  off best-matches to `OUTPUT_LVTTL33` — and **the pad still reads**, so the bit
  is hysteresis, not an input-buffer enable.
- `DRIVE 4` on CK clears `F2B6 F3B6`, sets `F6B6`, and decodes as
  `OUTPUT_SSTL15D_II` (both halves) or `OUTPUT_SSTL18D_I` (named half only). The
  board runs and scores the same cells in all three cases.
- So **a DRIVE or HYSTERESIS patch always renames the pad's IO standard on
  read-back.** `hyperram_pin_patch.py` prints each rename (`BASE_TYPE MOVED`)
  rather than refusing, because every one so far has been cosmetic.
- The enums are not per-PIO in the frames either: patching `dq[2]/dq[4]/dq[6]`
  DRIVE renames neighbouring `reset`'s BASE_TYPE in the same tile. `verify` in
  `hyperram_pin_patch.py` checks tile granularity, which is coarser than this.

## The workflow

    ./scripts/hyperram_pin_axis.py --label base --repeat 8 --rung 0
    ./scripts/hyperram_pin_axis.py --label hyst-off --against base \
        dq.HYSTERESIS=OFF rwds.HYSTERESIS=OFF

Per point: patch the built bitstream, configure, **confirm the board answers**,
record N matrix runs, diff them against each other and against a named control.

- The control is `--repack`: same `top.config`, same `ecppack`, so a point
  differs from its control only in PIO bits.
- `clk.p` / `clk.n` reach one half of a differential pair; bare `clk` is both.
- `apollo configure` returning 0 is **not** a running design. One attempt failed
  with "bitstream provides data past the device's SRAM array", the retry returned
  zero, and the FPGA was blank — no console, no USB. Scored naively that is a pin
  attribute killing the board. The gate is the board's own reply.

## What the artefacts record now

A pin-attribute run and its control share commit, firmware, CK and pass count;
everything that distinguished them was written nowhere. `results/hyperram/*.json`
gained:

- `bitstream` — path, size, **sha256** of the `.bit` that was configured.
- `pins` — every HyperRAM PIO's `BASE_TYPE`/`DRIVE`/`PULLMODE`/`SLEWRATE`/
  `HYSTERESIS`, unpacked from that `.bit`. `null` means unrecorded, not "the
  defaults".
- `axis_fail_counts` / `axes_live` — how failures fall across each matrix axis.

## Neither operating point can resolve it

Measured 2026-08-10, commit `3796d4f`, one board, one session.

### 80/90 MHz, non-DQS — noise floor 0-3 cells, and the rig is inert

`CYNTHION_HYPERRAM_BIST=1 CK_MHZ=80,90 BIST_DQS=0`.

- Noise floor: **0 cells** moved across 3 identical runs at 80 MHz; **3** across
  3 runs at 90 MHz (2 distinct marginal cells); **0** between two separate
  configures of the same bitstream.
- 896 of 4096 cells pass, and the pass set is **uniform** — exactly 112 per `sel`
  value, 112 per `drive` value, 448 per `clk` value. Only `lat` and `mode` move
  it. 896 = 7 × 128, so the pass set is decided entirely by `lat`/`mode`: seven
  of the thirty-two combinations pass all 128 of their cells, twenty-five fail
  all 128.
- **`sel` is uniform because it is not connected**, not because the rig is blind
  to it: on a non-DQS build nothing reads the register
  ([#343](https://github.com/awtoau/cynthion-workspace/issues/343)), so those
  4096 cells are 512 configurations run 8 times. That flatness is now expected
  and carries no information either way.
- The failure set is **byte-identical at 80 MHz and at 90 MHz**. A 12.5% change
  in clock frequency moves nothing. (The two runs were `baseline-a` and
  `base90-b`; every recorded matrix run was deleted with #353.)
- Disabling the CK output pad outright (`clk.BASE_TYPE=NONE`) also moved nothing.

The verdict stands, but not on `sel`: that axis is unwired here (#343), so its
flatness proves nothing. What is left still voids the build — **disabling the CK
pad moved nothing**, and a 12.5% change in CK moved nothing. Seven points were
run there — hysteresis off, CK# fast, CK slow, all-slow, CK
4 mA, DQ 4 mA, DQ pull-up, 16 mA everywhere — and all scored "no effect". **All
seven are void**, and are not in `results/`. `axes_live` exists so the next such
corpus is void on its face rather than after a day of it.

### 120 MHz, DQS — the rig is live and the noise floor is ~500 cells

`CYNTHION_HYPERRAM_BIST=1 CK_MHZ=120 BIST_DQS=1`. All five axes live.

Passing cells per run, 4096 cells, 2 passes each:

| block | n | mean | sd | runs |
|---|---|---|---|---|
| control (`dqs-base-c`) | 8 | 288.4 | 32.5 | 320 239 323 258 280 318 264 305 |
| `all.DRIVE=16` | 8 | 290.1 | 48.9 | 370 310 269 266 262 340 215 289 |
| `dq/rwds.HYSTERESIS=OFF` | 8 | 214.4 | 52.9 | 185 234 201 267 272 166 262 128 |
| control again (`dqs-base-d`) | 8 | 229.6 | 54.5 | 228 269 307 216 251 259 145 162 |

- **The control moved by 59 cells between its two blocks, ten minutes apart** —
  as large as the largest candidate effect. That is the whole result.
- Cell-level: 439-675 cells change verdict between two identical runs.
  32 passes/cell instead of 2 does not reduce it (416-519). Reconfiguring before
  every run does not reduce it (459-514). The variance is run-to-run state, not
  per-burst marginality.
- So a pin attribute must move >500 cells to be visible, and none did.
  `HYSTERESIS=OFF` looked like a 74-cell loss until the second control landed
  59 cells below the first.

Retained artefacts are two runs per block; the full series is the table above.

## Not tested

- The cross product. Four attributes over six pin groups; each point moves one
  attribute across one group.
- `OPENDRAIN`, a `.config_enum` here too, never touched.
- 160 MHz DQS, and anything above the non-DQS fabric cap (~94 MHz).
- Whether a rebuild with `DRIVE` in the LPF lands on the same bits as a patch.
  Assumed, not shown.

## What would make this measurable

The axis is not expensive; it is unresolvable. Either would fix it:

- **Fix the DQS read path's run-to-run drift** (#349). At a noise floor of 500
  cells nothing else in the matrix is resolvable either.
- **A non-DQS build that is actually live above 90 MHz.** Today's is inert at
  both its rungs, so its 0-3 cell noise floor buys nothing.
