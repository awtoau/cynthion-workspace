# Cynthion r1.4 Complete Pin Map (ECP5-12F / BG256)

**Source**: `/mnt/2tb/git/awtoau/awto-cynthion/cynthion/python/src/gateware/platform/cynthion_r1_4.py`

---

## Summary

| Category | Pin Count | Pins |
|----------|-----------|------|
| Pseudo-supply (VCCIO) | 31 | E6 E7 D10 E10 E11 F12 J12 K12 L12 N13 P13 M11 P11 P12 L4 M4 R5 M5 N5 P4 M6 F5 G5 H5 H4 J4 J5 J3 J1 J2 R6 |
| Pseudo-supply (GND) | 20 | E5 E8 E9 E12 F13 M13 M12 N12 N11 L5 L3 M3 N6 P5 P6 F4 G2 G3 H3 H2 |
| System Clock | 1 | A8 (60 MHz) |
| SPI Flash | 3 | T8, T7, N8 |
| QSPI Flash | 5 | T8, T7, M7, N7, N8 |
| UART (Debug) | 2 | R14 (RX), T14 (TX) |
| Interrupt | 1 | T6 |
| User Button | 1 | M14 |
| Self-Program | 1 | T13 |
| FPGA LEDs | 6 | E13, C13, B14, A15, D12, C11 |
| USB PHY (Control) | 8 | N16, N14, P16, P15, R16, R15, T15, P14, L14, M16, M15, L15, L16 |
| USB PHY (AUX) | 8 | F16, G15, G16, H15, J15, J16, K15, K16, D16, E16, F15, E15, J13 |
| USB PHY (Target) | 8 | R2, R1, P2, P1, N3, N1, M2, M1, T4, R3, T2, T3, R4 |
| Target USB Direct | 2 | N4 (DP), P3 (DM) |
| Type-C (Target) | 6 | A4, C4, A3, D4, A2, E4 |
| Type-C (AUX) | 6 | H12, G14, H14, J14, H13, K14 |
| Power Control | 6 | K13, L13, K5, L1, L2, K4 |
| Power Monitor (INA) | 5 | D7, C7, D5, C6, D6 |
| **HyperRAM** | **12** | **C3, D3, F2, B1, C2, E1, E3, E2, F3, G4, D1, B2, C1** |
| User PMOD A | 8 | C9, B9, D11, C12, C8, D8, D9, C10 |
| User PMOD B | 8 | B4, B5, B6, B7, C5, A5, A6, A7 |
| Mezzanine | 22 | B8, A9, B10, A10, B11, D14, C14, F14, E14, G13, G12, C16, C15, B16, B15, A14, B13, A13, D13, A12, B12, A11 |

---

## Detailed Pin List

### System

| Resource | Signal | Pin | Dir | Type | Notes |
|----------|--------|-----|-----|------|-------|
| **Clock** | clk_60MHz | A8 | I | LVCMOS33 | Primary oscillator, 60 MHz |
| **JTAG** | TDO | R11 | - | - | JTAG Test Data Out (shared with UART RX) |
| **JTAG** | TDI | R14 | - | - | JTAG Test Data In (shared with UART RX) |
| **JTAG** | TMS | T11 | - | - | JTAG Test Mode Select (shared with UART TX) |
| **JTAG** | TCK | - | - | - | JTAG Test Clock (dedicated pin) |

### Debug & Control

| Resource | Signal | Pin | Dir | Type | Notes |
|----------|--------|-----|-----|------|-------|
| **UART** | rx | R14 | I | LVCMOS33 | RX from debug controller |
| **UART** | tx | T14 | O | LVCMOS33 | TX to debug controller |
| **Interrupt** | int | T6 | O | LVCMOS33 | Signal to microcontroller |
| **Button** | user_button | M14 | I | LVCMOS33 | Active-low |
| **Self-Program** | self_program | T13 | O | LVCMOS33 | Trigger FPGA reconfig (active-low) |

### Oscillator & Clock Generation

| Resource | Pins | Dir | Function |
|----------|------|-----|----------|
| **clk_60MHz** | A8 | I | Discrete 60 MHz oscillator |
| **PLL** (internal) | - | - | Generates derived clocks for USB PHYs, HyperRAM |

### Flash Memory (SPI / QSPI)

| Resource | Signal | Pin | Dir | Type | Notes |
|----------|--------|-----|-----|------|-------|
| **SPI Flash** | sdi | T8 | O | LVCMOS33 | Serial Data In (MOSI) |
| **SPI Flash** | sdo | T7 | I | LVCMOS33 | Serial Data Out (MISO) |
| **SPI Flash** | cs | N8 | O | LVCMOS33 | Chip Select (active-low) |
| **QSPI Flash** | dq[0] | T8 | IO | LVCMOS33 | Quad Data (DQ0 / MOSI) |
| **QSPI Flash** | dq[1] | T7 | IO | LVCMOS33 | Quad Data (DQ1 / MISO) |
| **QSPI Flash** | dq[2] | M7 | IO | LVCMOS33 | Quad Data (DQ2) |
| **QSPI Flash** | dq[3] | N7 | IO | LVCMOS33 | Quad Data (DQ3) |
| **QSPI Flash** | cs | N8 | O | LVCMOS33 | Chip Select (active-low) |

### FPGA LEDs (6 total, active-low)

| LED # | Pin | Type | Notes |
|-------|-----|------|-------|
| LED 0 | E13 | LVCMOS33 | Active-low |
| LED 1 | C13 | LVCMOS33 | Active-low |
| LED 2 | B14 | LVCMOS33 | Active-low |
| LED 3 | A15 | LVCMOS33 | Active-low |
| LED 4 | D12 | LVCMOS33 | Active-low |
| LED 5 | C11 | LVCMOS33 | Active-low |

### USB PHYs (ULPI, 3x)

#### Control PHY

| Signal | Pins | Dir | Type | Notes |
|--------|------|-----|------|-------|
| data[0:7] | N16, N14, P16, P15, R16, R15, T15, P14 | IO | LVCMOS33 | 8-bit ULPI data bus |
| clk | L14 | O | LVCMOS33 | Output clock (synchronous PHY clock) |
| dir | M16 | I | LVCMOS33 | Direction (1=PHY→FPGA) |
| nxt | M15 | I | LVCMOS33 | Next (PHY handshake) |
| stp | L15 | O | LVCMOS33 | Stop (FPGA→PHY handshake) |
| rst | L16 | O | LVCMOS33 | Reset (active-low) |

#### AUX PHY

| Signal | Pins | Dir | Type | Notes |
|--------|------|-----|------|-------|
| data[0:7] | F16, G15, G16, H15, J15, J16, K15, K16 | IO | LVCMOS33 | 8-bit ULPI data bus |
| clk | D16 | O | LVCMOS33 | Output clock |
| dir | E16 | I | LVCMOS33 | Direction |
| nxt | F15 | I | LVCMOS33 | Next |
| stp | E15 | O | LVCMOS33 | Stop |
| rst | J13 | O | LVCMOS33 | Reset (active-low) |

#### Target PHY

| Signal | Pins | Dir | Type | Notes |
|--------|------|-----|------|-------|
| data[0:7] | R2, R1, P2, P1, N3, N1, M2, M1 | IO | LVCMOS33 | 8-bit ULPI data bus |
| clk | T4 | O | LVCMOS33 | Output clock |
| dir | R3 | I | LVCMOS33 | Direction |
| nxt | T2 | I | LVCMOS33 | Next |
| stp | T3 | O | LVCMOS33 | Stop |
| rst | R4 | O | LVCMOS33 | Reset (active-low) |

### USB Direct Connection (Target)

| Signal | Pin | Type | Dir | Notes |
|--------|-----|------|-----|-------|
| dp_diff (P) | N4 | LVDS | I | Differential pair |
| dm_diff (N) | P3 | LVDS | I | Differential pair |
| dp_single | N4 | LVCMOS33 | I | Single-ended version |
| dm_single | P3 | LVCMOS33 | I | Single-ended version |
| dp_chirp | N4 | LVCMOS12 | I | 1.2V for chirp detection |
| dm_chirp | P3 | LVCMOS12 | I | 1.2V for chirp detection |

### USB Type-C Controllers (2x)

#### Target Type-C (PD controller)

| Signal | Pin | Dir | Type | Notes |
|--------|-----|-----|------|-------|
| scl | A4 | O | LVCMOS33 | I2C clock (no pull) |
| sda | C4 | IO | LVCMOS33 | I2C data |
| int | A3 | I | LVCMOS33 | Interrupt (active-low, pull-up) |
| fault | D4 | I | LVCMOS33 | Fault (active-low, pull-up) |
| sbu1 | A2 | IO | LVCMOS33 | SBU1 mux |
| sbu2 | E4 | IO | LVCMOS33 | SBU2 mux |

#### AUX Type-C (PD controller)

| Signal | Pin | Dir | Type | Notes |
|--------|-----|-----|------|-------|
| scl | H12 | O | LVCMOS33 | I2C clock (no pull) |
| sda | G14 | IO | LVCMOS33 | I2C data |
| int | H14 | I | LVCMOS33 | Interrupt (active-low, pull-up) |
| fault | J14 | I | LVCMOS33 | Fault (active-low, pull-up) |
| sbu1 | H13 | IO | LVCMOS33 | SBU1 mux |
| sbu2 | K14 | IO | LVCMOS33 | SBU2 mux |

### Power Management

| Resource | Signal | Pin | Dir | Type | Notes |
|----------|--------|-----|-----|------|-------|
| **VBUS In** | control_vbus_in_en | K13 | O | LVCMOS33 | Enable control VBUS input (active-low) |
| **VBUS In** | aux_vbus_in_en | L13 | O | LVCMOS33 | Enable AUX VBUS input (active-low) |
| **VBUS Out** | target_c_vbus_en | K5 | O | LVCMOS33 | Enable target C VBUS output |
| **VBUS Out** | control_vbus_en | L1 | O | LVCMOS33 | Enable control VBUS output |
| **VBUS Out** | aux_vbus_en | L2 | O | LVCMOS33 | Enable AUX VBUS output |
| **Discharge** | target_a_discharge | K4 | O | LVCMOS33 | Discharge target A VBUS |

### Power Monitor (INA3221 or similar)

| Signal | Pin | Dir | Type | Notes |
|--------|-----|-----|------|-------|
| scl | D7 | O | LVCMOS33 | I2C clock (no pull) |
| sda | C7 | IO | LVCMOS33 | I2C data (pull-up enabled) |
| pwrdn | D5 | O | LVCMOS33 | Power-down (active-low) |
| slow | C6 | IO | LVCMOS33 | Slow timing (pull-up enabled) |
| gpio | D6 | IO | LVCMOS33 | GPIO (pull-up enabled) |

### HyperRAM (12 pins total)

**⭐ Key Signal**

| Signal | Pin | Dir | Type | DDR | Notes |
|--------|-----|-----|------|-----|-------|
| **clk[P]** | C3 | O | LVCMOS33D | - | Clock positive (differential) |
| **clk[N]** | D3 | O | LVCMOS33D | - | Clock negative (differential) |
| **dq[0]** | F2 | IO | LVCMOS33 | DDR | Data bit 0 |
| **dq[1]** | B1 | IO | LVCMOS33 | DDR | Data bit 1 |
| **dq[2]** | C2 | IO | LVCMOS33 | DDR | Data bit 2 |
| **dq[3]** | E1 | IO | LVCMOS33 | DDR | Data bit 3 |
| **dq[4]** | E3 | IO | LVCMOS33 | DDR | Data bit 4 |
| **dq[5]** | E2 | IO | LVCMOS33 | DDR | Data bit 5 |
| **dq[6]** | F3 | IO | LVCMOS33 | DDR | Data bit 6 |
| **dq[7]** | G4 | IO | LVCMOS33 | DDR | Data bit 7 |
| **rwds** | D1 | IO | LVCMOS33 | DDR | Read-Write Data Strobe |
| **cs** | B2 | O | LVCMOS33 | - | Chip Select (active-low) |
| **reset** | C1 | O | LVCMOS33 | - | Reset (active-low) |

**Attributes**: `IO_TYPE="LVCMOS33"`, `SLEWRATE="FAST"`

---

### User I/O Connectors

#### PMOD Connector A

```
Pin#  | FPGA Pin | Function
------|----------|----------
 1    | C9       | IO[0]
 2    | B9       | IO[1]
 3    | D11      | IO[2]
 4    | C12      | IO[3]
 5    | -        | GND
 6    | -        | -
 7    | C8       | IO[4]
 8    | D8       | IO[5]
 9    | D9       | IO[6]
 10   | C10      | IO[7]
 11   | -        | -
 12   | -        | GND
```

#### PMOD Connector B

```
Pin#  | FPGA Pin | Function
------|----------|----------
 1    | B4       | IO[0]
 2    | B5       | IO[1]
 3    | B6       | IO[2]
 4    | B7       | IO[3]
 5    | -        | GND
 6    | -        | -
 7    | C5       | IO[4]
 8    | A5       | IO[5]
 9    | A6       | IO[6]
10    | A7       | IO[7]
11    | -        | -
12    | -        | GND
```

#### Mezzanine Connector

```
Pin#  | FPGA Pin | Pin#  | FPGA Pin
------|----------|-------|----------
 1    | -        |  17   | -
 2    | -        |  18   | C16
 3    | B8       |  19   | C15
 4    | A9       |  20   | B16
 5    | B10      |  21   | B15
 6    | A10      |  22   | A14
 7    | B11      |  23   | B13
 8    | D14      |  24   | A13
 9    | C14      |  25   | D13
10    | F14      |  26   | A12
11    | E14      |  27   | B12
12    | G13      |  28   | A11
13    | G12      |  29   | -
14    | -        |  30   | -
15    | -        |  31   | -
16    | -        |  32   | -
```

---

### Pseudo-Supply Pins (for current sourcing/sinking)

**Pseudo VCCIO (Output to source current)** - 31 pins:
```
E6 E7 D10 E10 E11 F12 J12 K12 L12 N13 P13 M11 P11 P12 L4 M4 
R5 M5 N5 P4 M6 F5 G5 H5 H4 J4 J5 J3 J1 J2 R6
```

**Pseudo GND (Output to sink current)** - 20 pins:
```
E5 E8 E9 E12 F13 M13 M12 N12 N11 L5 L3 M3 N6 P5 P6 F4 G2 G3 H3 H2
```

These pins are connected to VCCIO or GND and can be driven as outputs to source or sink additional supply current for I/O buffering.

---

## Pin Distribution Summary

```
Total BG256 Package = 256 pins

Allocated to:
  - Power/GND (internal)
  - Pseudo-supply (51 pins for I/O current sourcing)
  - System (clock, JTAG, debug)
  - SPI/QSPI Flash interface
  - UART (debug)
  - 6x FPGA LEDs
  - 3x USB PHY interfaces (control, aux, target)
  - USB Type-C controllers (2x with I2C)
  - Power distribution & management
  - Power monitor (INA)
  - HyperRAM (12 pins) ⭐
  - User connectors (PMOD A/B + Mezzanine)
```

---

## Notes

1. **Differential Pairs**: 
   - HyperRAM clock (C3/D3) is differential LVCMOS33D
   - USB direct connection (N4/P3) can be LVDS or LVCMOS33

2. **Active-Low Signals** (marked with N):
   - self_program, user_button, CS, RESET
   - USB PHY resets
   - Type-C INT/FAULT
   - VBUS enable signals

3. **Shared Pins**:
   - R14: UART RX / JTAG TDI
   - T14: UART TX / JTAG TMS
   - N4/P3: USB direct (multiple I/O types possible)
   - T8/T7/N8: SPI and QSPI share pins

4. **Pull-up/Pull-down**:
   - Default: NONE (floating) unless specified
   - PULLMODE="UP" for interrupt/fault lines
   - PULLMODE="UP" for I2C/power monitor lines

5. **Slew Rate**:
   - FAST on: USB PHYs, HyperRAM, high-speed I/O
   - Default on: I2C, low-speed control signals

---

## Device Specs

- **FPGA**: Lattice ECP5-12F (LFE5U-12F)
- **Package**: BG256 (Ball Grid Array, 256 pins)
- **Speed Grade**: 8 (typically)
- **LUT Count**: 12,288 total
- **RAM**: 1.6 Mbit embedded
- **Clock**: Up to 380 MHz internal

