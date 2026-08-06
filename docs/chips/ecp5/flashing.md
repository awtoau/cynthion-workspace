# ECP5 Flashing — Cynthion Gateware Load Process

How bitstreams get onto the Cynthion's ECP5 FPGA, via the Apollo debug
controller over JTAG. Written against a Cynthion **r1.4** and verified on
hardware 2026-07-23.

## Two destinations, one JTAG link

Everything goes through Apollo's sideband JTAG connection to the ECP5. The only
question is where the bitstream lands:

| Target | CLI flag | Persistence | Implementation |
|---|---|---|---|
| **FPGA SRAM** | `--upload` / `-U` | Volatile — lost on power cycle | [`toolchain_program()`](../../repos/cynthion/cynthion/python/src/gateware/platform/core.py#L66) |
| **Configuration flash** | `--flash` / `-F` | Persistent — FPGA self-loads at boot | [`toolchain_flash()`](../../repos/cynthion/cynthion/python/src/gateware/platform/core.py#L88) |

Both call `ECP5_JTAGProgrammer` from `apollo_fpga.ecp5`; `configure()` writes
SRAM, `flash()` writes the SPI configuration flash.

Use `--upload` for iteration — it's faster and leaves nothing behind. Use
`--flash` when the design should survive a power cycle.

## Prerequisites

Toolchain (this workspace uses `~/.local/bin` and system yosys):

```
yosys           /usr/bin/yosys
nextpnr-ecp5    ~/.local/bin/nextpnr-ecp5
ecppack         ~/.local/bin/ecppack
apollo          ~/.local/bin/apollo
```

Python packages, installed editable into `.venv` from the repos:
`amaranth` (0.5.9), `luna`, `apollo_fpga`, `cynthion`.

Verify before building:

```bash
.venv/bin/python -c "import cynthion, apollo_fpga, luna, amaranth; print(amaranth.__version__)"
```

The device must be in **Apollo mode** (`1d50:615c`) for the build to autodetect
the platform. See [USB mode switching](#usb-mode-switching-the-main-gotcha).

## Building and loading

LUNA designs expose a standard CLI via `top_level_cli()`
([luna/__init__.py:33](https://github.com/greatscottgadgets/luna/blob/main/luna/__init__.py#L33)). Any gateware top
module is invoked the same way:

```bash
# Volatile load into FPGA SRAM
.venv/bin/python -m cynthion.gateware.selftest.top --upload --keep-files

# Persistent write to configuration flash
.venv/bin/python -m cynthion.gateware.selftest.top --flash

# Build only, no hardware needed
.venv/bin/python -m cynthion.gateware.selftest.top --dry-run --keep-files

# Build to a named bitstream file
.venv/bin/python -m cynthion.gateware.selftest.top -o my_design.bit
```

Useful flags:

- `--keep-files` — keeps build products in `build/` (`top.bit`, `top.json`,
  `top.config`, `top.svf`, `top.rpt`). Without it, LUNA builds in a temp dir and
  deletes it, so **the bitstream is discarded after upload**.
- `--erase` / `-E` — clears configuration flash before other operations.
- `--fpga <part>` — override the target part when no board is attached.
- `--console <port>` — opens a 115200 8N1 console right after upload.

Note `--flash` implies neither erase nor upload: flashing self-reconfigures the
FPGA and implicitly erases, so LUNA clears both flags
([luna/__init__.py:85-87](https://github.com/greatscottgadgets/luna/blob/main/luna/__init__.py#L85-L87)).

### Loading a pre-built bitstream

To push an existing `.bit` without rebuilding, use Apollo directly — this is
what the retired [`cyn_main.py:969`](../../../debris/scripts/cyn_main.py#L969) did, before
`./dev.py` replaced it:

```bash
apollo configure build/top.bit
```

## Build settings

The Cynthion platform pins `ecppack` options
([core.py:60-62](../../repos/cynthion/cynthion/python/src/gateware/platform/core.py#L60-L62)):

```python
overrides = {'ecppack_opts': '--compress --freq 38.8'}
```

`--compress` shrinks the bitstream; `--freq 38.8` sets the SPI configuration
clock for flash boot. A reference selftest bitstream is ~100 KB compressed.

## USB mode switching (the main gotcha)

The Cynthion shares **one USB port** between Apollo and the FPGA gateware. Only
one can own it at a time, and this drives most of the operational friction.

| VID:PID | Owner | Meaning |
|---|---|---|
| `1d50:615c` | Apollo Debugger | Apollo has the port — builds and JTAG work |
| `1d50:615b` | USB Analyzer | FPGA gateware has the port — Apollo is a stub |

After a successful `--upload`, `toolchain_program()` calls
`allow_fpga_takeover_usb()`
([core.py:83](../../repos/cynthion/cynthion/python/src/gateware/platform/core.py#L83)),
handing the port to the freshly loaded gateware. **The device re-enumerates
mid-session.** Any open Apollo handle dies at this point — typically as
`[Errno 110] Operation timed out` or a `[Errno 32] Pipe error` in the JTAG
teardown.

Check current mode:

```bash
lsusb | grep 1d50
```

### Recovering from FPGA-owned mode

Once in `615b`, a plain `ApolloDebugger()` raises:

```
DebuggerNotFound: Apollo stub interface found but not requested to be forced offline.
```

Take the port back:

```python
from apollo_fpga import ApolloDebugger
d = ApolloDebugger(force_offline=True)
```

**`force_offline=True` drops the SRAM bitstream.** It resets the FPGA to get the
port back, so any `--upload`ed design is gone and register reads fail with:

```
OSError: Failed to autonegotiate meta-JTAG address/register size.
```

The recovery cycle is therefore: force offline → re-upload → reconnect.

A build attempted while in `615b` mode fails at platform autodetect:

```
RuntimeError: Unable to autodetect a supported platform.
The LUNA_PLATFORM environment variable must be set.
```

That is a symptom of wrong USB mode, not a missing env var — force offline
first, then rebuild.

## Verifying a load

The selftest gateware exposes an ID register that confirms the debug link
reaches the loaded design
([gateware.py:39](../../repos/cynthion/cynthion/python/src/selftest/gateware.py#L39)):

```python
from apollo_fpga import ApolloDebugger
d = ApolloDebugger()
assert d.registers.register_read(1) == 0x54455354   # "TEST"
```

If this reads back correctly, JTAG register access to the gateware is working.
Two ready-made checks in this workspace:

```bash
.venv/bin/python scripts/selftest_leds.py   # LED register walk  -> tmp/selftest_leds.log
.venv/bin/python debris/scripts/phy_probe.py       # ULPI PHY data lines -> tmp/phy_probe.log
```

Note `selftest_leds.py` verifies the LED *register* round-trips — it reads back
the register, not the pin, so it cannot detect a physically dead LED. It leaves
`101010` on the board for visual confirmation.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `DebuggerNotFound: ... not requested to be forced offline` | FPGA owns USB (`615b`) | `ApolloDebugger(force_offline=True)` |
| `DebuggerNotFound: No Apollo debugger or stub interface found` | Device absent or unpowered | Check `lsusb`, replug |
| `RuntimeError: Unable to autodetect a supported platform` | Building while in `615b` | Force offline, then rebuild |
| `Failed to autonegotiate meta-JTAG address/register size` | No gateware in SRAM | Re-upload the bitstream |
| `[Errno 110] timed out` / `[Errno 32] Pipe error` mid-run | Re-enumeration on FPGA takeover | Expected after upload; reconnect |
| Bitstream missing after upload | Built without `--keep-files` | Rebuild with `--keep-files` or `-o` |

Before blaming USB contention, confirm it — check for a driver bound to the
interfaces and whether the port is actually claimable:

```bash
lsof /dev/bus/usb/001/*
for d in /sys/bus/usb/devices/*/; do
  [ "$(cat $d/idVendor 2>/dev/null)" = "1d50" ] && ls -l $d*:*/driver 2>/dev/null
done
```

On a healthy idle device both come back empty — no driver bound, nothing
holding the nodes.

## Related

- [Cynthion selftest gateware](../../repos/cynthion/cynthion/python/src/gateware/selftest/top.py) — buildable top used in the examples above
- [Apollo selftest harness](../../../repos/apollo/apollo_fpga/support/selftest.py) — `ApolloSelfTestCase`, `@named_test`
- [debris/scripts/cyn_main.py](../../../debris/scripts/cyn_main.py) — the retired `cyn flash gateware`, wired to `build/top.bit`; `./dev.py` is the entry point now
- [docs/hardware.md](../../hardware.md) — the board index
- [docs/chips/w25q32-config-flash.md](../w25q32-config-flash.md) — the flash part itself
