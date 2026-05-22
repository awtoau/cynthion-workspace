# Cynthion Architecture Scan Report

**Date**: 2026-05-22  
**Scope**: awtoau repositories (apollo, cynthion, luna, saturn-v, facedancer, packetry, hardware)  
**Focus**: UART/Serial communication design decisions, pulse-train mechanism, watchdog/failsafe design

## Key Findings

### 1. ApolloAdvertiser Implementation (apollo_fpga/gateware/advertiser.py)

**Design**: Generates a 20ms square wave on FPGA_ADV pin (PA09)
- Half-period: 10ms (calculated as 10% of 100ms in code comment)
- Creates rising edges every 10ms
- = ~10 rising edges per 200ms detection window

**Added**: Commit 0b4b855 (Oct 16, 2023) by mndza (Diego)
```
apollo_fpga.gateware: add ApolloAdvertiser

When this is added as a submodule to a design, an advertisement message
is sent periodically to Apollo. Apollo will take over the port when
these announcements are interrupted or when the PROGRAM button is
pressed.
```

**Code Location**: `awto-apollo/apollo_fpga/gateware/advertiser.py:42-48`
```python
# Generate clock with 20ms period
half_period = int(self.clk_freq_hz * 10e-3)  # 10ms
timer = Signal(range(half_period))
clk = Signal()
m.d.sync += timer.eq(Mux(timer == half_period-1, 0, timer+1))
with m.If((timer == 0) & (~self.stop)):
    m.d.sync += clk.eq(~clk)  # Toggle clock every 10ms
```

### 2. Apollo FPGA_ADV Detection Logic (awto-apollo/firmware/src/boards/cynthion_d11/fpga_adv.c)

**Detection Window**: 200ms (line 27)
```c
#define WINDOW_PERIOD_MS 200UL
```

**Detection Threshold**: >2 rising edges in 200ms window (line 125)
```c
bool fpga_requesting_port(void) {
    return window_edges > 2;  // >2 edges = FPGA wants port
}
```

**Implementation**: Uses EIC (External Interrupt Controller) to count rising edges
- EIC configured on line 56: `gpio_set_pin_function(FPGA_ADV, MUX_PA09A_EIC_EXTINT7);`
- Interrupt handler counts edges on every rising edge (line 141)
- Every 200ms, window_edges = edge_counter, then counter reset (lines 87-90)

**USB Port Switching Logic** (lines 93-97):
```c
if (fpga_requesting_port() == false) {
    take_over_usb();      // No pulse-train = Apollo takes port
} else if (fpga_usb_allowed) {
    hand_off_usb();       // Pulse-train detected = hand to FPGA
}
```

### 3. Pin Assignments (awto-apollo/firmware/src/boards/cynthion_d11/apollo_board.h)

**FPGA Control Pins**:
- FPGA_PROGRAM = PA08 (reset signal)
- FPGA_ADV = PA09 (pulse-train for USB negotiation)
- FPGA_INITN = PA03 (initialization status)
- FPGA_DONE = PA04 (configuration done status)

**JTAG Pins** (conflict with current UART):
- TMS = PA11 (conflicted with UART RX)
- TDI = PA14 (conflicted with UART TX)
- TCK = PA15
- TDO = PA10

### 4. Current UART Configuration

**Location**: `awto-apollo/firmware/src/boards/cynthion_d11/uart.c:34-43`

**Current Pinmux** (CONFLICTS WITH JTAG):
```c
static void _uart_configure_pinmux(bool use_for_uart) {
    if (use_for_uart) {
        gpio_set_pin_function(PIN_PA11, MUX_PA11D_SERCOM2_PAD3);  // RX (JTAG TMS)
        gpio_set_pin_function(PIN_PA14, MUX_PA14D_SERCOM2_PAD0);  // TX (JTAG TDI)
    }
}
```

**SERCOM2 Configuration** (lines 99-102):
```c
sercom->USART.CTRLA.reg =
    SERCOM_USART_CTRLA_TXPO(0)    |  // TX on PAD[0] = PA14
    SERCOM_USART_CTRLA_RXPO(3)    |  // RX on PAD[3] = PA11
    ...
```

### 5. Unimplemented SPI_FPGA_DEBUG

**Location**: `awto-apollo/firmware/src/boards/cynthion_d11/spi.c`

**Status**: TODO on line 56
```c
case SPI_FPGA_DEBUG:
    // TODO  <-- NEVER IMPLEMENTED
    break;
```

**Clocking Already Set Up** (lines 92-95):
```c
case SPI_FPGA_DEBUG:
    _pm_enable_bus_clock(PM_BUS_APBC, SERCOM2);
    _gclk_enable_channel(SERCOM2_GCLK_ID_CORE, GCLK_CLKCTRL_GEN_GCLK0_Val);
    break;
```

**Analysis**: Someone previously planned to use SERCOM2 for FPGA debug SPI, set up the clocking, but never completed the pinmux configuration. This was abandoned in favor of SERCOM0 for JTAG SPI.

**Note** (line 29): `// Alternatively, SERCOM2.` - Shows SERCOM2 was considered for SPI JTAG but rejected in favor of SERCOM0.

### 6. moondancer Firmware Integration

**Location**: `awto-cynthion/firmware/moondancer/src/bin/moondancer.rs`

**USB Port Takeover Logic**:
```rust
let advertiser = peripherals.ADVERTISER;
advertiser.control().write(|w| w.enable().bit(true));  // Enable pulse-train
```

**Shutdown Handler**:
```rust
let advertiser = unsafe { pac::ADVERTISER::steal() };
advertiser.control().write(|w| w.enable().bit(false));  // Disable pulse-train on exit
```

**Vulnerability**: No watchdog or failsafe mechanism if firmware panics/hangs between enable and disable.

### 7. Advertiser CSR in FPGA

**Location**: `awto-cynthion/cynthion/python/src/gateware/facedancer/advertiser.py`

**Control Register** (line 19-25):
```python
class Control(csr.Register, access="w"):
    """enable : Set this bit to '1' to start ApolloAdvertiser and disconnect
                the Cynthion USB control port from Apollo.
    """
    enable : csr.Field(csr.action.W, unsigned(1))
```

**Logic** (line 57-58):
```python
with m.If(self._control.f.enable.w_stb):
    m.d.sync += stop.eq(~self._control.f.enable.w_data)
```

**Note**: Inverted logic - writing 1 to enable sets stop=0 (start pulse-train)

### 8. Design Questions & Missing Pieces

**No Evidence Found For**:
- Why UART wasn't used from the beginning
- Investigation into gate count constraints
- Prior UART-based design attempts
- Design decision documentation or discussion

**No "rover" Label In Code**:
- Searched all .py, .rs, .c, .h files
- "rover" appears to be an issue/organization label only, not in commit messages or code

**Unimplemented Features**:
- SPI_FPGA_DEBUG never completed
- No bidirectional communication mechanism
- No watchdog timeout protection

## Architecture Vulnerability

The current design creates a **single point of failure**:

1. moondancer firmware enables advertiser pulse-train
2. Apollo detects pulse-train and hands off USB port
3. **If moondancer crashes**: Advertiser CSR keeps running
4. **If moondancer firmware panics**: No handler to disable advertiser
5. **Result**: Host loses all USB access (even CONTROL port)

## Recommended Solution Path

Replace pulse-train with hardware UART using:
- **PA08** → SERCOM2 TX (currently FPGA_PROGRAM, used only post-bootup)
- **PA09** → SERCOM2 RX (currently FPGA_ADV pulse-train, can be repurposed)
- **PA03** ← watchdog interrupt (FPGA_INITN, status pin)
- **PA04** ← status feedback (FPGA_DONE, status pin)

**Advantages**:
1. Bidirectional synchronous communication
2. Moondancer can send heartbeat responses
3. Apollo can detect moondancer timeout and force reset
4. JTAG pins freed for actual JTAG debugging
5. Uses native SERCOM2 hardware (no bit-banging)

## References

**Files Examined**:
- awto-apollo/apollo_fpga/gateware/advertiser.py - Pulse-train generator
- awto-apollo/firmware/src/boards/cynthion_d11/fpga_adv.c - Edge detection logic
- awto-apollo/firmware/src/boards/cynthion_d11/uart.c - Current UART on JTAG pins
- awto-apollo/firmware/src/boards/cynthion_d11/spi.c - SPI configuration (unimplemented DEBUG variant)
- awto-apollo/firmware/src/boards/cynthion_d11/apollo_board.h - Pin definitions
- awto-cynthion/firmware/moondancer/src/bin/moondancer.rs - Advertiser control
- awto-cynthion/cynthion/python/src/gateware/facedancer/advertiser.py - CSR wrapper

**Related Commits**:
- 0b4b855: apollo_fpga.gateware: add ApolloAdvertiser (Oct 2023)
- 6decb7d: gateware: ApolloAdvertiser generates rising edges now
- f68195e: apollo_fpga.gateware: convert request handler to new interface

**No References To**:
- Issue #15 in git history
- Prior watchdog implementations
- Gate count constraint discussions
- UART design alternative evaluations
