# ECP5 pin usage — every declared pin, and whether anything drives it

An audit of the r1.4 pin map against the design that runs on the board today, the
RISC-V SoC in [`gateware/soc/top.py`](../../../gateware/soc/top.py).

**Index:** [`../../hardware.md`](../../hardware.md) ·
**pin map:** [`gateware/board/cynthion_r1_4.py`](../../../gateware/board/cynthion_r1_4.py)
(vendored byte-for-byte from
[`repos/cynthion/cynthion/python/src/gateware/platform/cynthion_r1_4.py`](../../../repos/cynthion/cynthion/python/src/gateware/platform/cynthion_r1_4.py);
the resource lists are identical, so this audit covers both)

## Why this exists

The PAC1954 has two ALERT-capable pins. Both reach the FPGA, both are inside a
resource the SoC requests, and neither is connected to anything — the part can
raise an interrupt and there is no wire on this side to receive it. That is not a
bug in any one line; it is a class of thing that no test can find, because a pin
connected to nothing behaves exactly like a pin that is not there.

So: every subsignal in the pin map, in one of three states.

* **used** — requested, and driven or read
* **requested-unused** — the resource *is* requested, usually for a sibling
  subsignal, and this pin is connected to nothing
* **never requested** — the resource is not requested by this design at all

The interesting column is the middle one. A never-requested resource is a
decision; a requested-unused pin is usually an oversight that survived because
the surrounding code works.

Cross-checked against the board, not only the source: net membership comes from
[`repos/cynthion-hardware/production/netlist.ipc`](../../../repos/cynthion-hardware/production/netlist.ipc)
(IPC-356, 256 `IC1` records) and values from
`repos/cynthion-hardware/production/bom.csv` -- NOT a link, because that file is
gitignored upstream (`*.csv`) and exists in no clone. See [#376](https://github.com/awtoau/cynthion-workspace/issues/376).
A pin that the board terminates is a different finding from one that is free, and
in two cases below the termination is the whole story.

---

## Summary

| | count |
|---|---|
| ECP5 balls in the package (`IC1` records) | 256 |
| balls declared in the pin map | 183 |
| balls not declared: dedicated supply/ground | 48 |
| balls not declared: configuration and JTAG | 11 |
| balls not declared: **no board net at all** | 14 |
| declared balls the SoC **uses** | 121 |
| declared balls **requested and left unconnected** | **6** |
| declared balls in resources the SoC **never requests** | 56 |

121 + 6 + 56 = 183, so every declared ball is accounted for. The 56 break down as
`user_mezzanine` 22, the two PMODs 16, `control_phy` 13, N4/P3, K13/L13 and T13.

The requested-unused balls are the PAC1954's `slow` (C6) and
the four Type-C `sbu1`/`sbu2` pins (A2, E4, H13, K14).

---

## The table

Balls are as the pin map writes them. "Where" names the file that drives or reads
the pin.

### Supply pseudo-pins

| resource | subsignal | balls | state | where / why |
|---|---|---|---|---|
| `pseudo_vccio` 0 | — | E6 E7 D10 E10 E11 F12 J12 K12 L12 N13 P13 M11 P11 P12 L4 M4 R5 M5 N5 P4 M6 F5 G5 H5 H4 J4 J5 J3 J1 J2 R6 | used | [`gateware/board/core.py`](../../../gateware/board/core.py) `pseudo_power_supply_fragment`, added to every build by `append_fragments`. Netlist confirms each ball is on `+3V3` or `VCCRAM` |
| `pseudo_gnd` 0 | — | E5 E8 E9 E12 F13 M13 M12 N12 N11 L5 L3 M3 N6 P5 P6 F4 G2 G3 H3 H2 | used | same; netlist confirms all 20 are on `GND` |

### Clock, flash, JTAG-adjacent

| resource | subsignal | balls | state | where / why |
|---|---|---|---|---|
| `clk_60MHz` 0 | — | A8 | used | [`gateware/soc/clocks.py`](../../../gateware/soc/clocks.py) — the PLL reference |
| `qspi_flash` 0 | `dq` | T8 T7 M7 N7 | used | `QSPIFlashPins` in [`gateware/soc/peripherals/flash.py`](../../../gateware/soc/peripherals/flash.py) |
| | `cs` | N8 | used | same |
| `spi_flash` 0 | `sdi` `sdo` `cs` | T8 T7 N8 | **never requested** | Deliberate. Strict subset of `qspi_flash`'s balls; requesting both is a pin conflict. See *Deliberately unused* |
| `uart` 0 | `rx` | R14 | used | [`gateware/soc/top.py`](../../../gateware/soc/top.py) → `SerialLine` |
| | `tx` | T14 | used | same, `dir="oe"` — released when idle because T14 **is** JTAG TMS |
| `int` 0 | — | T6 | used | `SidebandDebug`, [`gateware/probes/sideband/sideband_debug.py`](../../../gateware/probes/sideband/sideband_debug.py). Bidirectional; net `DEBUGGER/FPGA_ADV` → SAMD11 U6 pin 8 |
| `self_program` 0 | — | T13 | **never requested** | Nothing in this workspace requests it. Board fits `R5` (2.2 kΩ) from T13 to `FPGA_CONFIG.~{PROGRAM}` (R9) — see the ranked list |

### LEDs and button

| resource | subsignal | balls | state | where / why |
|---|---|---|---|---|
| `led` 0–5 | — | E13 C13 B14 A15 D12 C11 | used | [`gateware/soc/top.py`](../../../gateware/soc/top.py); red, orange, yellow, green, blue, violet in that order (BOM D2–D7) |
| `button_user` 0 | — | M14 | used | GPIO pin 7. Board: `R106` 10 kΩ pull-up, `SW3` to GND, `R107` 33 kΩ series, `C78` |

### USB PHYs

| resource | subsignal | balls | state | where / why |
|---|---|---|---|---|
| `aux_phy` 0 | all 13 | F16 G15 G16 H15 J15 J16 K15 K16 / D16 / E16 / F15 / E15 / J13 | used | `USBSerialDevice` (CDC-ACM console). LUNA's ULPI interface drives `clk` and `rst` itself |
| `target_phy` 0 | all 13 | R2 R1 P2 P1 N3 N1 M2 M1 / T4 / R3 / T2 / T3 / R4 | used | register path only, via `peripherals/ulpi_window.py` — no UTMI translator on this port |
| `control_phy` 0 | all 13 | N16 N14 P16 P15 R16 R15 T15 P14 / L14 / M16 / M15 / L15 / L16 | **never requested** | Apollo's port. Taking it needs the advertising handshake (`apollo_port_sharing = {'control_phy': 'advertising'}`) |

### Direct TARGET USB taps — five declarations, two balls

| resource | subsignal | balls | state | where / why |
|---|---|---|---|---|
| `target_usb_dp` 0 | — | N4 | **never requested** (by the SoC) | Requested only by [`gateware/probes/pins/pin_survey.py`](../../../gateware/probes/pins/pin_survey.py). Net `TARGET_FS_MONITOR_D+`, 1 kΩ (`R58`) in series from `TARGET_A_D+` |
| `target_usb_dm` 0 | — | P3 | **never requested** (by the SoC) | as above, `R57` |
| `target_usb_diff` 0 | — | N4 P3 | never requested | LVDS view of the same two balls |
| `target_usb_dp_chirp` 0 | — | N4 | never requested | LVCMOS12 view, for chirp detection |
| `target_usb_dm_chirp` 0 | — | P3 | never requested | LVCMOS12 view |

These five are mutually exclusive: at most one may be requested in a design.

### Type-C controllers

| resource | subsignal | ball | state | where / why |
|---|---|---|---|---|
| `target_type_c` 0 | `scl` | A4 | used | I2C mux segment `TARGET`, `R98` 2.2 kΩ pull-up |
| | `sda` | C4 | used | `R97` 2.2 kΩ |
| | `int` | A3 | used | → `i2c_mux.target_int` → CSR **and** `plic.sources[4]`. `R37` 2.2 kΩ, FUSB302B `U2` pin 5 |
| | `fault` | D4 | used | → `i2c_mux.target_fault` → CSR. `R100` 10 kΩ; source is `U13` pin 6 `FAULTB`, open-collector, on the DPO2036 — **not** the FUSB302B |
| | `sbu1` | A2 | **requested-unused** | net `TARGET_C.SBU1S` → `U13` pin 10 → `J4` A8 |
| | `sbu2` | E4 | **requested-unused** | net `TARGET_C.SBU2S` → `U13` pin 9 → `J4` B8 |
| `aux_type_c` 0 | `scl` | H12 | used | `R38` 2.2 kΩ |
| | `sda` | G14 | used | `R33` 2.2 kΩ |
| | `int` | H14 | used | → `plic.sources[5]`. `R39` 2.2 kΩ, FUSB302B `U12` pin 5 |
| | `fault` | J14 | used | `R99` 10 kΩ, from `U14` |
| | `sbu1` | H13 | **requested-unused** | net `AUX_TYPE_C.SBU1S` → `U14` pin 9 → `J1` A8 |
| | `sbu2` | K14 | **requested-unused** | net `AUX_TYPE_C.SBU2S` → `U14` pin 10 → `J1` B8 |

Detail on the parts: [`../fusb302b-type-c.md`](../fusb302b-type-c.md).

### VBUS control

| resource | ball | state | where / why |
|---|---|---|---|
| `target_c_vbus_en` 0 | K5 | used | `VbusControl`, [`gateware/soc/peripherals/vbus_csr.py`](../../../gateware/soc/peripherals/vbus_csr.py) |
| `control_vbus_en` 0 | L1 | used | same |
| `aux_vbus_en` 0 | L2 | used | same |
| `target_a_discharge` 0 | K4 | used | same |
| `control_vbus_in_en` 0 | K13 | **never requested** | `VbusControl` *has* an output for it and firmware *has* commands that write it. See the ranked list — this is the worst finding here |
| `aux_vbus_in_en` 0 | L13 | **never requested** | same |

### Power monitor

| resource | subsignal | ball | state | where / why |
|---|---|---|---|---|
| `power_monitor` 0 | `scl` | D7 | used | I2C mux segment `POWER`, `R83` 2.2 kΩ |
| | `sda` | C7 | used | `R84` 2.2 kΩ |
| | `pwrdn` | D5 | used | GPIO pin 6. `R87` 5.1 kΩ pull-up, PAC1954 `U1` pin 16 |
| | `slow` | C6 | **requested-unused** | PAC1954 `U1` pin 1 = SLOW/ALERT1. **`R85`, 10 kΩ to +3V3, is fitted** |
| | `gpio` | D6 | used | PAC1954 `U1` pin 15 = GPIO/ALERT2 → `plic.sources[IRQ_POWER_ALERT]`, inverted. **`R86`, 10 kΩ to +3V3, is fitted**. The source is not in the firmware's enable mask — see [`../../soc-interrupts.md`](../../soc-interrupts.md) |

Part detail: [`../pac1954-power-monitor.md`](../pac1954-power-monitor.md).

### HyperRAM

| resource | subsignal | balls | state | where / why |
|---|---|---|---|---|
| `ram` 0 | `clk` | C3 D3 | used | requested `dir="-"` by `BootRAM` in [`gateware/soc/bootram.py`](../../../gateware/soc/bootram.py); `HyperRAMDQSPHY` owns the buffers |
| | `dq` | F2 B1 C2 E1 E3 E2 F3 G4 | used | same |
| | `rwds` | D1 | used | same |
| | `cs` | B2 | used | same |
| | `reset` | C1 | used | same |

### User I/O

| resource | balls | state | where / why |
|---|---|---|---|
| `user_pmod` 0 | C9 B9 D11 C12 C8 D8 D9 C10 (`J7`) | **never requested** (by the SoC) | driven only by [`gateware/probes/pins/pin_survey.py`](../../../gateware/probes/pins/pin_survey.py) |
| `user_pmod` 1 | B4 B5 B6 B7 C5 A5 A6 A7 (`J8`) | **never requested** (by the SoC) | same |
| `user_mezzanine` 0 | B8 A9 B10 A10 B11 D14 C14 F14 E14 G13 G12 C16 C15 B16 B15 A14 B13 A13 D13 A12 B12 A11 (`J5`) | **never requested, anywhere in this tree** | 22 balls. The largest block of unused I/O on the board |

The connector maps check out against the netlist exactly: `J7`/`J8` pins 5, 6, 11,
12 are GND/+3V3 (the `-` entries in the `pmod` connectors), `J5` pins 2, 14, 15,
16, 17, 29 are GND/+3V3 and pins 1 and 30 have no net — which is why the
`mezzanine` connector lists `-` at exactly those positions.

---

## Ranked: unused capability worth having

Ranked by what it buys against what it costs, worst-first where "worst" means the
design currently believes something untrue.

### 1. `control_vbus_in_en` / `aux_vbus_in_en` — a register that reads back and does nothing

**State:** never requested. But
[`gateware/soc/peripherals/vbus_csr.py`](../../../gateware/soc/peripherals/vbus_csr.py)
declares `control_vbus_in_en` and `aux_vbus_in_en` as component outputs, gives
them a dedicated CSR (`vbus` +0x01, `control_in` init 1, `aux_in` init 0), and
documents at length why they reset the way they do — including a boot-loop
consequence. [`gateware/soc/top.py`](../../../gateware/soc/top.py) connects the
other four `VbusControl` outputs to pads and leaves these two unconnected, with a
comment saying so deliberately.

Firmware follows the peripheral, not the top level:
[`firmware/cynthion-soc/src/vbus.rs`](../../../firmware/cynthion-soc/src/vbus.rs)
has `read_input`, `write_input`, `inputs()` and `allow_all_inputs()`. Those write
a register that goes nowhere.

**Why it matters more than an unused pin:** the two source files contradict each
other, and the one a reader is most likely to open — the peripheral, with the long
docstring — is the one that is wrong. Everything downstream inherits it: the shell
command reads back what it wrote and looks correct.

**What it would take:** either (a) two `platform.request(...)` lines in
`top.py` wiring the two existing outputs to K13/L13, which makes the docstring
true; or (b) delete the outputs, the CSR register and the firmware commands, which
makes the top-level comment true. Option (a) needs care — the docstring's boot-loop
warning becomes real the moment the pins are connected, and the reset values
(`control_in`=1, `aux_in`=0) then decide whether a board powered from AUX alone
comes up at all.

**Not established:** what K13/L13 actually sit at while unrequested. Both go to
transistor bases (`R71`→`Q11A`, `R78`→`Q11B`), not to a rail, so the board pull
does not settle it by inspection. What *is* known is that the board runs normally
on CONTROL power with this bitstream loaded, so the undriven state leaves the
CONTROL input closed. Establishing the AUX one needs a measurement at `L13` with
the SoC configured, or the `power` reading with AUX alone attached.

### 2. PAC1954 `slow` (C6) — not merely unused; it is holding the ADC at 8 SPS

**State:** requested-unused in the SoC. Both probe designs
([`gateware/probes/power_monitor/power_monitor_gateware.py`](../../../gateware/probes/power_monitor/power_monitor_gateware.py)
and
[`gateware/probes/sideband/sideband_gateware.py`](../../../gateware/probes/sideband/sideband_gateware.py))
drive it low; `top.py` does not.

The chain, each link sourced:

* the board fits **`R85`, 10 kΩ from C6 to +3V3** (netlist; BOM 10 kΩ group), so
  the net is high whenever nothing drives it low — the FPGA's `PULLMODE="UP"` on
  this resource agrees with the board rather than fighting it;
* the PAC195X `CTRL` register (`0x01`) resets with `SLOW_ALERT1 = 0b11`, "SLOW
  functions as the SLOW pin" (DS20006539B Register 7-2);
* [`firmware/cynthion-soc/src/power.rs`](../../../firmware/cynthion-soc/src/power.rs)
  writes exactly one configuration register, `NEG_PWR_FSR` (`0x1D`). `CTRL` is
  never written, so the reset assignment stands;
* datasheet §3.8: with the pin high the sampling rate is **8 SPS** regardless of
  the programmed `SAMPLE_MODE`, which is otherwise 1024 SPS adaptive.

So the SoC's power monitor is converting once per **125 ms** while the firmware
poller issues a REFRESH every 50 ms. Roughly three polls in five should return the
values the previous one already saw, and the `sampled N ms ago` line on `power`
would then understate true measurement age by up to a conversion period.

**Caveat, stated because it changes what to do about it:** the rate is inferred
from datasheet plus netlist plus source, and has **not** been measured on the
board. What would settle it: apply a step load to a rail and time how long
`power` takes to move, with `slow` undriven and then driven low; 125 ms against
~1 ms is not a subtle difference.

**What it would take:** two lines in `top.py` — `power_monitor.slow.o.eq(0)` and
`.slow.oe.eq(1)` — matching what the two probe designs already do. That restores
1024 SPS and costs nothing.

**Also fix:** [`../pac1954-power-monitor.md`](../pac1954-power-monitor.md) says
under *Known limitations* that "`SLOW` is driven low for the 1024 SPS default".
That is true of the probes and false of the SoC, which is the design that ships.

### 3. PAC1954 `gpio` (D6) — wired to the interrupt controller, not enabled by the firmware

**State:** `plic.sources[IRQ_POWER_ALERT].eq(~power_monitor.gpio.i)` in
`gateware/soc/top.py`. `R86`, 10 kΩ to +3V3, is fitted — what the datasheet
requires for the ALERT function, since the pin is open-drain (§3.9).

The firmware's enable mask is `0000003e`, bits 1–5, so nothing listens yet.
Whether that is deliberate is unrecorded — [`../../soc-interrupts.md`](../../soc-interrupts.md).

**What the part can do with it** (DS20006539B §5.16, Registers 7-2, 7-20, 7-22,
7-34):

* **ALERT on conversion complete** — a 5 µs low pulse at the end of each round-
  robin cycle. Replaces "poll every 50 ms and hope" with "read when there is
  something new", and removes the REFRESH-window race that
  [#123](https://github.com/awtoau/cynthion-workspace/issues/123) worked around by
  making the poller the sole owner of the bus.
* **Threshold alerts** — per-channel overvoltage, undervoltage, overcurrent,
  undercurrent and overpower, with a programmable number of consecutive samples
  over the limit before it fires. This is the guard
  [`../../hardware.md`](../../hardware.md) describes as needed for a USB-A device
  on TARGET-A that cannot refuse 20 V, and it is currently a firmware polling loop
  that can only be as fast as its interval.
* **Accumulator-full / overflow alerts**, so accumulated charge can be read before
  it wraps rather than on a schedule chosen by guesswork.

Threshold alerts latch until the cause register (`0x26`) is read; conversion-
complete does not latch and sets no status bit, so it must be caught as an edge.

**What it would take:**

1. `top.py`: read `power_monitor.gpio.i` through an `FFSynchronizer` (the same
   shape `i2c_mux` already uses for `int`/`fault`), plus an edge detector for the
   5 µs pulse.
2. `Plic(sources=5)` → `6`, one new `IRQ_*` constant, one `plic.sources[...]`
   line. The pattern is already there twice for the two Type-C `int` lines.
3. Firmware: write `CTRL` (`0x01`) with `GPIO_ALERT2 = 0b00`, enable the wanted
   alerts in `ALERT_ENABLE` (`0x49`), route them to the pin in `GPIO_ALERT2`
   (`0x28`), and read `ALERT STATUS` (`0x26`) in the handler.

**Prefer D6 to C6 for this.** `GPIO/ALERT2` defaults to a plain input, so
reassigning it changes nothing else; reassigning `SLOW/ALERT1` gives up the SLOW
function on that pin (datasheet: "the SLOW function of this pin cannot be used
once the ALERT" function is assigned), and finding #2 wants that function.

### 4. The four SBU pins — Type-C sideband, proven alive, wired to nothing

**State:** requested-unused on both ports. A2/E4 (TARGET-C) and H13/K14 (AUX) run
through the DPO2036 protection switches `U13`/`U14` to `J4`/`J1` pins A8/B8. Plain `dir="io"`
FPGA pins with a protection part in between and nothing else on the net.

[#97](https://github.com/awtoau/cynthion-workspace/issues/97) already drove and
read back all four successfully — so the pads, the buffers and the DPO2036 are known
good. That is a survey result sitting idle.

**What SBU1/SBU2 are for:** they are the Type-C sideband pair. In DisplayPort alt
mode they carry AUX; in **Debug Accessory Mode** they are the standard place a
UART appears — the pattern a Chromebook-style debug cable uses to reach a device's
serial console. Cynthion has the pins on two ports, behind overvoltage clamps,
with a PD controller on each of those same two ports to negotiate the mode.

**What it would take:** a small amount. They are ordinary bidirectional pins, so
the minimum is a CSR that drives and reads them (an hour), and the useful version
is an `AsyncSerial` on one pair plus the FUSB302B side of Debug Accessory Mode
entry, which is firmware work on top of the Type-C stack already present in
[`firmware/cynthion-soc/src/fusb302.rs`](../../../firmware/cynthion-soc/src/fusb302.rs).

**Not established:** whether the DPO2036 passes a signal cleanly enough for a
megabaud UART. [#97](https://github.com/awtoau/cynthion-workspace/issues/97) proves DC continuity, not bandwidth. The datasheet for that
part is not in [`sources/`](../../../sources/README.md) — fetching it is the first
step for anyone taking this on.

### 5. `self_program` (T13) — the FPGA cannot currently reload itself

**State:** never requested by anything in this workspace. `R5` (2.2 kΩ) ties T13 to
the `FPGA_CONFIG.~{PROGRAM}` net, which is `IC1` R9 (PROGRAMN) and also SAMD11
`U6` pin 7.

**What it is for:** driving it asserted pulls PROGRAMN low and starts a
reconfiguration from flash — the FPGA-initiated half of the multiboot story that
[`flash-partitioning.md`](flash-partitioning.md) and
[`programming-paths.md`](programming-paths.md) describe. Today every reload is
Apollo-initiated, so a bitstream cannot hand over to another bitstream without a
host.

**What it would take:** one `platform.request` and a CSR bit, plus a hard decision
about what guards it — a register that reconfigures the FPGA is a register that
can lose a debugging session. Note
[`reconfigure-initn-gap.md`](reconfigure-initn-gap.md): the existing
Apollo-triggered path already leaves INITN held low, and the same trap applies
here.

### 6. `target_usb_dp` / `target_usb_dm` (N4, P3) — a PHY-independent view of TARGET-A

**State:** never requested by the SoC; only by the pin survey. The nets are
`TARGET_FS_MONITOR_D±`, 1 kΩ in series (`R57`, `R58`) from `TARGET_A_D±`.

**`repos/cynthion-hardware` is a fork with the Type-A socket removed**
(`awtoau/awto-cynthion-hardware`, one commit above upstream `13aa71c`). In that
tree these nets, `R57`/`R58` and `TARGET_A_D±` are all absent, so the FPGA-side
stubs read as orphaned. Everything on this page describes **upstream r1.4**.

**What it is for:** raw line-state on the TARGET port without going through the
USB3343. That answers questions the ULPI register path cannot — is the bus idle,
is a device asserting a pull-up, is a reset or a chirp happening — and it keeps
working when the PHY is held in reset or has glitched. The LVCMOS12 `*_chirp`
declarations exist because a high-speed chirp is a ~400 mV differential swing that
an LVCMOS33 input will not see.

**What it would take:** request one view of the pair (they are mutually exclusive),
synchronise into `sync`, and expose two bits plus edge counters. Small. The
constraint to respect is that requesting `target_usb_dp` forecloses
`target_usb_diff` and the chirp views in the same bitstream.

### 7. `user_mezzanine` (22 balls) and the two PMODs (16 balls)

**State:** the PMODs are exercised by the pin survey; `user_mezzanine` is requested
by **nothing in this tree at all**. 38 general-purpose I/O balls, on connectors,
with no consumer.

Not a defect — they are user I/O and the user is whoever builds a mezzanine. Worth
recording because "38 free I/O" is the answer to a question that otherwise gets
re-derived every time someone needs a pin for an ILA trigger or a logic-analyser
tap.

**Not established:** whether `user_mezzanine` survives a loopback test. The PMODs
were surveyed under [#97](https://github.com/awtoau/cynthion-workspace/issues/97); the mezzanine was in scope for that work but the results
are not recorded in this tree. Running the existing survey applet with a mezzanine
group added would settle it in minutes.

### 8. `control_phy` (13 balls)

**State:** never requested. This is Apollo's port unless the gateware advertises
for it. Worth listing only so the next audit does not count it as free: taking it
costs a handshake, not a pin.

---

## The reverse: pins driven that are not declared

Two, and both are legitimate — neither ball can be declared as an ordinary
resource on an ECP5.

| ball | net | how it is driven | why it is not in the pin map |
|---|---|---|---|
| N9 | `FPGA_FLASH_CLK` (W25Q32 `U7` pin 6) | the `USRMCLK` macro, instantiated in [`gateware/soc/peripherals/flash.py`](../../../gateware/soc/peripherals/flash.py) | the configuration clock has no user I/O buffer; the macro is the only route. The pin map says so in the `spi_flash` comment |
| R11, T10, T11, M10 | `FPGA_JTAG.TDI` / `.TCK` / `.TMS` / `.TDO` | the die's single `JTAGG` primitive, via [`gateware/soc/bus/jtag_stage.py`](../../../gateware/soc/bus/jtag_stage.py) | dedicated JTAG pins, not fabric I/O |

Note the overlap that *is* declared: **R14 and R11 are the same net**, and **T14
and T11 are the same net** — confirmed in the netlist, not merely asserted by the
pin map's comment. So the `uart` resource and the JTAG tap share wires, which is
why `tx` is `dir="oe"` and why the SoC never transmits unbidden.

## Resources requested twice

**None.** Amaranth raises on a second request, so this is enforced rather than
audited — but three overlapping *declarations* exist and are worth naming, because
requesting two members of a group is the error that would be raised:

* `spi_flash` and `qspi_flash` share T8, T7 and N8;
* `target_usb_dp`, `target_usb_dm`, `target_usb_diff`, `target_usb_dp_chirp` and
  `target_usb_dm_chirp` are five views of N4 and P3;
* `power_monitor` is requested once, in `top.py`, and *passed* to the I2C mux
  rather than requested again — the top level says so explicitly, which is the
  right shape.

## Balls with no board net

Fourteen `IC1` balls are single-pad nets in the netlist — no via, no second
component, no connection of any kind:

> B3, F1, G1, K1, K2, K3, M8, M9, P7, P8, R7, R8, R12, R13

They cannot be used for anything external. Recorded so the next audit does not
count them as free I/O, and so that a future revision knows where the spare balls
are.

The other 59 undeclared balls are 48 dedicated supply/ground and 11 configuration
and JTAG (`M10` TDO, `R11` TDI, `T10` TCK, `T11` TMS, `N9` MCLK, `N10`/`P10`/`R10`
CFG0–2, `P9` DONE, `R9` PROGRAMN, `T9` INITN).

---

## Deliberately unused — do not re-report these

| thing | reason |
|---|---|
| `spi_flash` | Strict subset of `qspi_flash`'s balls. The SoC uses quad mode; requesting both is a conflict, not an option |
| `target_usb_diff`, `*_dp_chirp`, `*_dm_chirp` | Alternative electrical views of N4/P3. Exactly one member of the group can ever be requested |
| `control_phy` | Apollo owns the CONTROL port. Reachable through advertising, and that is a protocol decision rather than a pin one |
| `user_pmod` 0/1, `user_mezzanine` | User I/O with no user in this tree. Free by design |
| `pseudo_vccio`, `pseudo_gnd` | Used, but never by a design — the platform's `append_fragments` drives them in every build. If a design appears to leave them alone, that is correct |
| PAC1954 exposed pad | The datasheet's "it is recommended that you connect it to ground" is §3.10, about the **exposed thermal pad**, not about SLOW/ALERT1. `U1` pin 17 is on `GND` — the board already does this. Nothing to do |
| `control_vbus_in_en` / `aux_vbus_in_en` | Currently justified in `top.py` as deliberate — **but** see finding #1. This entry is not settled and should be re-reported until the peripheral, the firmware and the top level agree |

---

## Method, so this can be re-run

1. Resource list read from
   [`gateware/board/cynthion_r1_4.py`](../../../gateware/board/cynthion_r1_4.py)
   and diffed against upstream's copy — identical.
2. Every `platform.request(` call reachable from
   [`gateware/soc/top.py`](../../../gateware/soc/top.py) enumerated: `top.py`
   itself, plus `clocks.py`, `bootram.py`, `peripherals/flash.py`,
   `probes/sideband/sideband_debug.py` and `board/core.py`.
3. Per requested resource, each subsignal checked for an assignment in either
   direction.
4. Ball set diffed against the 256 `IC1` records in the netlist, which produced
   the unconnected-ball list and confirmed every declared ball exists.
5. Net membership for each pin of interest pulled from the same netlist to find
   terminations — which is where `R85` and `R86` turned up, and they changed two
   findings from "unused" to "unused, and here is the consequence".

Datasheet references are to
[`sources/PAC195X-Family-DS20006539B.pdf`](../../../sources/PAC195X-Family-DS20006539B.pdf);
see [`sources/README.md`](../../../sources/README.md) for how to tell a good copy
from a truncated one.
