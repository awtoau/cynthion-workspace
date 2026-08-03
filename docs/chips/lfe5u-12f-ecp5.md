# ECP5 `LFE5U-12F` — the FPGA, and it is a 25F die

The main programmable device on Cynthion r1.4. Lattice ECP5, marked `LFE5U-12F`,
CABGA256, speed grade 8.

**Index:** [`../hardware.md`](../hardware.md)

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

Who actually consumes it: [`../luna_ecp5_fpga/bram-budget.md`](../luna_ecp5_fpga/bram-budget.md).

## DSP blocks

The ECP5 has DSP blocks, so a hardware multiplier is cheap. Measured at **16
cycles** for an integer multiply against 123 for a soft-float single-precision
multiply — which is why `rv32im` earns its area on this part. Recorded in
[`../moondancer/riscv_state_of_play.md`](../moondancer/riscv_state_of_play.md).

## Clocking

One discrete **60 MHz oscillator on pin A8**. Everything else comes from the PLL.

The PLL is driven by `VariableClockDomainGenerator`
(`repos/apollo/apollo_fpga/gateware/variable_clock.py`), not upstream's
`LunaECP5DomainGenerator`, because upstream offers only 60/120/240 MHz from
hardcoded taps. Ours solves for `sync` **and** `usb` together so `usb` lands on
exactly 60 MHz — `ecppll` optimises its primary output and lets the secondary fall
where it may, and a `usb` clock 3.7% out does not enumerate the ULPI PHY. See
[`../upstream-boundary.md`](../upstream-boundary.md) and #111.

How fast the soft CPU can be clocked on this part:
[`../riscv-clock-ceiling.md`](../riscv-clock-ceiling.md).

## How software reaches it

| path | mechanism |
|---|---|
| configuration over JTAG | Apollo bit-bangs the TAP; `apollo` CLI |
| configuration from flash | at power-on, from the [W25Q32](w25q32-config-flash.md) at offset 0 |
| debug registers / ILA | JTAG ER1 (`0x32`) / ER2 (`0x38`) tunnel, via `JTAGRegisterInterface` |
| reconfigure | Apollo drives PROGRAMN (MCU PA08); the fabric can also self-trigger via `self_program` (T13) |

JTAG pins on the FPGA side are **R11 (TDI)** and **T11 (TMS)**, which are wired to
the UART pins R14/T14 — see the pin-sharing section of
[`../hardware.md`](../hardware.md).

## Registers

**SoC peripheral registers are not documented here.** The SoC's own memory map is
the authority — see [Register reference](../hardware.md#register-reference) in the
board index. This note covers the silicon, not the gateware running on it.

## Code and scripts

| | |
|---|---|
| pin map (vendored) | `ecp5-test/cynthion_platform/cynthion_r1_4.py` |
| fabric test gateware | `ecp5-test/fabric/fabric_gateware.py` |
| build / run / control | `scripts/fabric_build.py`, `fabric_run.py`, `fabric_control.py`, `fabric_placement.py`, `fabric_sim.py`, `fabric_golden.py` |
| flashing and configuration | [`../luna_ecp5_fpga/ecp5-flashing.md`](../luna_ecp5_fpga/ecp5-flashing.md) |
| live opcode sweep | [`../luna_ecp5_fpga/dynamic-opcode-probe.md`](../luna_ecp5_fpga/dynamic-opcode-probe.md), `scripts/ecp5_cmd_probe.py` |

Generic ECP5/toolchain findings live in pluribus (`docs/ecp5/`), not here — the
test is whether a finding would be useful to someone with a different ECP5 board.
