# Does quad-SPI speed up ECP5 configuration from flash?

Testing whether `ecppack --spimode qspi` makes the Cynthion r1.4 boot faster
from its SPI configuration flash. Investigated 2026-07-29 on a Cynthion r1.4
(serial `<board serial redacted>`, LFE5U-12F, Winbond W25Q32 `ef4016`).

## Answer

**Not measured. The boot timing could not be run on this board**, for a reason
that is itself a definite finding and is documented below. The build-side half
of the question is fully answered and verified on hardware-independent
evidence.

What is established:

- `--spimode` **does** reach `ecppack` and **does** change the bitstream, once
  the platform's hardcoded options are worked around (they otherwise raise a
  `TypeError`). Verified per variant, both in `build_top.sh` and byte-wise.
- `--freq` accepts **only four values** — 2.4, 19.4, 38.8 and 62.0 MHz.
- Lane count and configuration clock are **independent** fields in the
  bitstream, so they can be varied separately.
- Configuration-from-flash does **not complete** on this board under host
  control, because `INITN` is held low. This blocks the timing measurement and
  is a real behaviour of the board plus current Apollo firmware, not a test
  artifact.

Whether quad SPI actually shortens boot time on this hardware is **untested**.
No timing number in this document is measured, and none is estimated.

## What `--spimode` does to the bitstream

Measured by diffing bitstreams built from the identical `top.config`. Each mode
inserts a four-byte command immediately after the `FFFFBDB3` preamble; the
baseline inserts nothing at all.

| `--spimode` | Bytes inserted after preamble | Bitstream size |
|---|---|---|
| *(omitted)* | — none — | 100336 |
| `fast-read` | `79 49 00 00` | 100340 |
| `dual-spi`  | `79 51 00 00` | 100340 |
| `qspi`      | `79 59 00 00` | 100340 |

`0x79` is the ECP5 `SPI_MODE` opcode (`apollo_fpga/ecp5.py:155`). The operand
differs only in bits 3–4 (`0x49`/`0x51`/`0x59` = `0b0100_1001` /
`0b0101_0001` / `0b0101_1001`), which is the lane-count selector. So the option
is a genuine, minimal change to how the configuration engine reads flash — it
is not a no-op, and it does not disturb the rest of the bitstream.

## What `--freq` does, and what it accepts

`ecppack` rejects anything outside a fixed set. Determined by trying the whole
documented ECP5 MCLK table against it:

```
ACCEPTED: 2.4  19.4  38.8  62.0
REJECTED: 3.1 4.3 5.4 6.9 8.1 9.2 10.0 13.0 13.9 15.65 20.8 24.8 27.8
          31.25 33.3 41.6 45.0 51.0 55.6 80.0 100.0
```

Two things follow. First, the sweep in any experiment here is four points, not
a continuum. Second — and usefully — **62.0 MHz is both the top of ecppack's
list and the top of Lattice's MCLK frequency table** (FPGA-TN-02039). The tool
will not let you request an out-of-spec configuration clock, so the
"out of spec if it appears to work" hazard noted for *user-mode* flash reads
(see [spi-flash-summary.md](spi-flash-summary.md), where 80 MHz worked but was
out of spec) does not arise for *configuration*.

`--freq` writes one field at bitstream offset `0x38`, inside control register 0,
plus a CRC fixup at `0x57`/`0x58`:

| `--freq` | byte at `0x38` |
|---|---|
| 2.4  | `0x00` |
| 19.4 | `0x30` |
| 38.8 | `0x38` |
| 62.0 | `0x3b` |

It never touches the `0x79` SPI-mode command, and `--spimode` never touches
`0x38`. **The two knobs are orthogonal**, which is what makes a lane-count
vs clock-rate comparison meaningful in principle.

## Passing the options through Amaranth

The obvious approach does not work. `CynthionPlatform.toolchain_prepare`
hardcodes the options and forwards them alongside the caller's kwargs
(`repos/cynthion/cynthion/python/src/gateware/platform/core.py:59-64`):

```python
def toolchain_prepare(self, fragment, name, **kwargs):
    overrides = {'ecppack_opts': '--compress --freq 38.8'}
    return super().toolchain_prepare(fragment, name, **overrides, **kwargs)
```

so `platform.build(design, ecppack_opts="--spimode qspi")` raises:

```
TypeError: TemplatedPlatform.toolchain_prepare() got multiple values for
keyword argument 'ecppack_opts'
```

Subclassing and calling `super()` does not help either — the hardcoding lives
in `CynthionPlatform`, one level below `CynthionPlatformRev1D4`, so `super()`
lands right back on it. The working form bypasses that method entirely:

```python
from amaranth.build.plat import TemplatedPlatform

class Plat(CynthionPlatformRev1D4):
    def toolchain_prepare(self, fragment, name, **kwargs):
        kwargs["ecppack_opts"] = "--compress --freq 38.8 --spimode qspi"
        return TemplatedPlatform.toolchain_prepare(self, fragment, name, **kwargs)
```

Confirmed in the generated `build_top.sh` rather than assumed:

```sh
"$ECPPACK" --compress --freq 38.8 --spimode qspi --input top.config --bit top.bit --svf top.svf
```

## Why the timing could not be measured

### The intended method

Trigger and endpoint were both worked out and are sound:

- **Start** — Apollo's `REQUEST_RECONFIGURE` (`soft_reset()`), which pulses
  `PROGRAMN` (`firmware/src/boards/cynthion_d11/fpga.c:52`).
- **End** — the FPGA's `DONE` pin, read through Apollo vendor request `0xc4`
  (`VENDOR_REQUEST_GET_FPGA_STATUS_PINS`, returning bit 0 = DONE,
  bit 1 = INITN).

Measured cost of that round trip: **0.214 ms**, ~4700 samples/s. That is ample
for an effect predicted to be tens of milliseconds.

USB enumeration was deliberately rejected as the endpoint. A ~288 KiB bitstream
at 38.8 MHz single-lane is roughly 60 ms of transfer, ~15 ms quad — a
difference of tens of ms, whereas host-side enumeration latency is hundreds of
ms and varies run-to-run by more than the entire effect. Polling `DONE` avoids
the USB stack completely.

The `DONE` endpoint was calibrated against a known-good state rather than
trusted: after a successful JTAG `configure`, `0xc4` reports `DONE=1`. It
correctly reports `DONE=0` when the FPGA is unconfigured.

### The blocker

**`INITN` is held low, so configuration never completes.**

From the r1.4 schematic (`repos/cynthion-hardware/bank8_configuration.kicad_sch`,
cross-checked against `production/netlist.ipc` and `production/bom.csv`):

| Net | FPGA ball | Termination |
|---|---|---|
| `INITN` | T9 | **R102, 5.1 kΩ to GND — a pull-DOWN. No pull-up anywhere on the board.** |
| `PROGRAMN` | R9 | R6, 5.1 kΩ to +3V3 (pull-up) |
| `DONE` | P9 | no external resistor at all |

`INITN` on the ECP5 is open-drain and must be released **high** for
configuration to proceed. With a hard pull-down and no pull-up, the only thing
that can raise it is the SAMD11 actively driving it — which is exactly what
`permit_fpga_configuration(true)` does
(`firmware/src/boards/cynthion_d11/fpga.c:22-35`).

That function is called **only at Apollo startup** (`firmware/src/main.c:74`
and `:81`). No vendor request re-invokes it. `REQUEST_RECONFIGURE` pulses
`PROGRAMN` but never re-drives `INITN`, so once `force_fpga_offline()` has run,
a host-triggered reconfiguration cannot succeed no matter how it is invoked.

The ECP5's own status register confirms the diagnosis rather than leaving it
inferred. Read as the first JTAG action in a fresh process (so nothing in the
session had put the part into configuration mode):

```
status = 0x00006000
  DONE       : 0
  ISC enable : 0
  Busy       : 0
  Fail       : 1
  BSE error  : 0   (no CRC / preamble / ID error)
```

**Fail set with error code 0** — configuration was attempted and abandoned, and
the bitstream itself was never rejected. That is the signature of the part
being held in initialisation, not of bad data.

Confirming that the data really was fine:

- The bitstream was written to flash and **read back byte-exact** (`match: True`).
- The *same* bitstream loads successfully over JTAG (`DONE=1` afterwards).

Every host-side trigger was tried and all fail identically, with `INITN=0`
throughout: `REQUEST_RECONFIGURE`; JTAG `LSC_REFRESH` (0x79, "equivalent to
toggling the PROGRAMN pin"), which does clear `DONE` and so genuinely starts a
reconfiguration; `ISC_DISABLE` followed by refresh; and repeated `0xc4` calls,
whose handler sets `INITN` to an input (`firmware/src/vendor.c:201`) and so
releases Apollo's end of the line — `INITN` still reads 0, because the board's
pull-down holds it there.

A USB port reset does not help: it re-enumerates the device without rebooting
the SAMD11, so Apollo's startup path never re-runs (verified — `INITN` stayed
low across a `device.reset()`).

### What would unblock it

Either:

1. **A physical power cycle or RESET button press between variants.** Apollo's
   startup path then runs, drives `INITN` high and pulses `PROGRAMN` — the real
   power-on boot path. This needs someone at the board, so it cannot be
   automated from here, but it is the faithful test.
2. **An Apollo firmware change**: call `permit_fpga_configuration(true)` inside
   the `REQUEST_RECONFIGURE` handler before pulsing `PROGRAMN`. This looks like
   a genuine gap — `trigger_fpga_reconfiguration()` re-enables the FPGA in
   software (`fpga_set_online(true)`) but never restores the pin that permits
   configuration, so the documented "reconfigure from flash" command cannot
   work after a `force-offline`.

## Board state as left

The Cynthion is **in the Saturn-V DFU bootloader** (`1d50:615c`, "Cynthion
Bootloader"), reached while attempting to reset the SAMD11 to re-run its
startup path. It is healthy and enumerating with the correct serial number;
`dfu-util -l` sees it.

**No firmware was written over DFU** — only detach requests, which the
bootloader refused (`dfu-util: can't detach`), and a read attempt that the
bootloader blocks by design. The application firmware is untouched.

**Recovery: unplug and replug the board.** Saturn-V jumps to the application on
a power-on reset; a USB-level reset is not enough.

Configuration flash currently holds the `baseline-38.8` test bitstream (a
6-LED blinker), written and verified byte-exact.

### On the flash backup

`tmp/qspiboot/flash-backup-full.bin` is a full 4 MiB read taken before any
writes. Worth knowing what it is: it contains **no ECP5 bitstream at all** — no
`Part: LFE5U` header, no `FFFFBDB3` preamble anywhere in 4 MiB. The two
populated regions (`0x000000–0x03cac2`, `0x0b0000–0x0be24b`) are ARM firmware
images. The board's configuration flash held no gateware when this work
started, which is consistent with `reconfigure` producing no FPGA device before
anything was changed. So nothing bootable was overwritten.

## Variants built

All ten build cleanly and all carry the intended options, verified in
`build_top.sh` and by bitstream diff:

| Variant | `--spimode` | `--freq` | Bytes | Boot time |
|---|---|---|---|---|
| `baseline-38.8` | *(none)* | 38.8 | 100336 | not measured |
| `fast-read-38.8` | `fast-read` | 38.8 | 100340 | not measured |
| `dual-spi-38.8` | `dual-spi` | 38.8 | 100340 | not measured |
| `qspi-38.8` | `qspi` | 38.8 | 100340 | not measured |
| `baseline-2.4` | *(none)* | 2.4 | 100336 | not measured |
| `baseline-19.4` | *(none)* | 19.4 | 100336 | not measured |
| `baseline-62.0` | *(none)* | 62.0 | 100336 | not measured |
| `qspi-2.4` | `qspi` | 2.4 | 100340 | not measured |
| `qspi-19.4` | `qspi` | 19.4 | 100340 | not measured |
| `qspi-62.0` | `qspi` | 62.0 | 100340 | not measured |

The design is a fixed 6-LED blinker, identical across all ten, so the only
variable is how `ecppack` packs it.

Note this is a ~100 KB bitstream for a trivial design, not the ~288 KB of a
real one. Any eventual measurement should be repeated with a
representative design, since configuration time scales with bitstream size and
the fixed overheads do not.

## Relationship to the existing flash-speed work

[spi-flash-summary.md](spi-flash-summary.md) measured **user-mode** reads from
this flash through gateware: 3.75 MB/s single-lane at 30 MHz, 23.9 MB/s quad at
48 MHz — a clean 4× from lane count. That is what motivates this experiment.

It does not settle it, for two reasons. Those reads run *after* configuration,
driving `MCLK` through `USRMCLK` at a clock the design chooses; configuration
reads run *before* any user logic exists, at one of four clocks the silicon
offers, driven by the configuration engine. And the ECP5's configuration engine
decompresses while it loads, so whether the flash read is even the bottleneck
during boot is an open question — if decompression or frame-writing dominates,
quad could deliver much less than the 4× the raw-throughput figures suggest.

That is precisely the kind of negative result worth having, and it remains
unresolved.

## Reproducing

```bash
./scripts/qspi_boot_time.py build    # build all ten variants
./scripts/qspi_boot_time.py verify   # confirm options reached ecppack
./scripts/qspi_boot_time.py measure  # flash + time (blocked, see above)
```

Logs land in `tmp/logs/qspi_boot_time.log`. `build` and `verify` work today and
need no hardware. `measure` writes flash and verifies every write before timing
it; it currently reports a timeout per variant and logs an explicit warning
when it sees `INITN=0`.

## Files

| Path | What |
|---|---|
| `scripts/qspi_boot_time.py` | Build / verify / measure driver |
| `tmp/qspiboot/<variant>/top.bit` | The ten bitstreams |
| `tmp/qspiboot/flash-backup-full.bin` | 4 MiB pre-change flash image (no gateware in it) |
| `repos/cynthion/.../platform/core.py:59` | The hardcoded `ecppack_opts` |
| `repos/apollo/firmware/src/boards/cynthion_d11/fpga.c:22` | `permit_fpga_configuration` |
| `repos/apollo/firmware/src/main.c:74` | Its only callers, both at startup |
| `repos/apollo/firmware/src/vendor.c:192` | The `0xc4` DONE/INITN request |
