# Apollo (SAMD11 ARM Cortex-M0+ MCU) - Moondancer (RISC-V softcore on ECP5 FPGA) Communication Architecture Redesign

**Status**: Design Proposal  
**Label**: `rover`  
**Related**: Issue #15 (firmware+gateware: use Apollo ARM supervisor for watchdog)

## Executive Architecture Summary

Apollo (SAMD11 ARM Cortex-M0+ MCU) is treated as the always-on supervisor for
moondancer (RISC-V softcore on ECP5 FPGA).

Current risk without this redesign:
- no robust watchdog protection for moondancer hangs
- manual restart required after certain firmware failures

Supervision model:
1. moondancer sends periodic health responses
2. Apollo monitors link health and timeout conditions
3. Apollo asserts reset when watchdog criteria are violated
4. moondancer recovers automatically after reset

Execution phases:
1. protocol and signaling design
2. Apollo supervisor firmware changes
3. moondancer and gateware integration
4. validation and failure-mode testing

## Problem Statement

### Current Architecture Issues

The current Apollo (SAMD11 ARM Cortex-M0+ MCU) ↔ Moondancer (RISC-V softcore on ECP5 FPGA) communication uses a **crude pulse-train signaling** mechanism that creates several problems:

1. **USB Port Switching Vulnerability**
   - When moondancer firmware loads, it asserts `FPGA_ADV` pin repeatedly
   - Apollo counts pulses (>2 edges in 200ms window) to detect "FPGA wants USB"
   - Apollo then **surrenders the CONTROL USB port** to moondancer
   - **If moondancer crashes**: Host loses all USB communication and cannot reset FPGA

2. **Limited Signaling Bandwidth**
   - Only one-directional: moondancer→Apollo via pulse train
   - Apollo→moondancer: only PROGRAM_B (reset) and status pins
   - No bidirectional data exchange

3. **Design Conflict with JTAG**
   - Current UART implementation uses PA11(JTAG TMS) and PA14(JTAG TDI)
   - Forces choice between UART console and JTAG debugging
   - Creates pin allocation bottleneck

4. **Hardware Mismatch**
   - 6+ individual GPIO pins needed (PROGRAM, ADV, INITN, DONE, SWITCH, etc.)
   - Each needs software bit-banging
   - No hardware peripheral integration

### Root Cause Analysis

The pulse-train approach appears to be a workaround for:
- Lack of available pins for a proper UART interface
- Attempt to signal moondancer's readiness without adding new hardware
- Possible gate count constraints (though unclear)

**Question**: Why wasn't UART used from the start? PA08/PA09 support SERCOM2 UART natively.

---

## Proposed Solution: UART-Based Watchdog

### Architecture Overview

Replace the pulse-train mechanism with **hardware UART on Apollo's SERCOM2**, using the existing 4 wired pins:

```
Apollo (ATSAMD11 MCU)        FPGA (ECP5 SoC)
════════════════════════════════════════════════════

PA08 → SERCOM2 TX ────────→ moondancer UART RX
PA09 ← SERCOM2 RX ────────← moondancer UART TX
PA03 ← GPIO INT (watchdog) ← moondancer INT out
PA04 ← GPIO status ────────← moondancer status

JTAG pins freed:
PA14 (TDI) ← available
PA11 (TMS) ← available
```

### Benefits

1. **True Bidirectional Communication**
   - Apollo can send heartbeat/commands to moondancer
   - moondancer can respond with status/diagnostics
   - Potential for remote firmware queries

2. **Resolves USB Switching Problem**
   - Apollo stays on CONTROL USB port **always**
   - No port surrendering needed
   - moondancer uses TARGET-A USB for facedancer (existing)
   - Host always has access to Apollo even if moondancer crashes

3. **Frees JTAG Pins**
   - PA11, PA14 no longer needed for UART
   - JTAG debugging still available via PA10/PA14/PA15/PA11 (current GPIO bit-bang)
   - Could enable SERCOM0 SPI for other use cases

4. **Hardware Peripheral Integration**
   - Uses native SERCOM2 UART (no bit-banging)
   - Clock-synchronized communication
   - Better reliability and debugging

5. **Simple Protocol**
   ```
   Heartbeat (Apollo→moondancer):
     0x5A → "are you alive?"
   
   Response (moondancer→Apollo):
     0x5A 0x00 → "alive, no errors"
     0x5A 0x01 → "alive, minor issue"
     0x5A 0xFF → "alive, but degraded"
   
   Watchdog timeout (moondancer→Apollo):
     INT line assertion (PA03 goes low)
     Apollo responds by asserting PROGRAM_B reset
   ```

---

## Implementation Plan

### Phase 1: Apollo Firmware

**File**: `awto-apollo/firmware/src/boards/cynthion_d11/uart.c`

Change SERCOM2 UART pinmux from PA11/PA14 (JTAG) to PA08/PA09:

```c
// OLD (conflicts with JTAG):
gpio_set_pin_function(PIN_PA11, MUX_PA11D_SERCOM2_PAD3);  // RX
gpio_set_pin_function(PIN_PA14, MUX_PA14D_SERCOM2_PAD0);  // TX

// NEW (uses FPGA control pins):
gpio_set_pin_function(PIN_PA08, MUX_PA08D_SERCOM2_PAD0);  // TX
gpio_set_pin_function(PIN_PA09, MUX_PA09D_SERCOM2_PAD1);  // RX
```

**Add watchdog handler**:
```c
void apollo_watchdog_task(void) {
    // Send heartbeat every 10ms
    if (timer_expired()) {
        uart_send_byte(0x5A);
        expect_response_timeout = 50ms;
    }
    
    // Check for watchdog interrupt (PA03)
    if (gpio_read(PA03) == 0) {  // INT asserted
        // moondancer timeout detected
        gpio_set_pin_level(FPGA_PROGRAM, 0);  // Assert reset
        msleep(10);
        gpio_set_pin_level(FPGA_PROGRAM, 1);  // Release reset
    }
}
```

### Phase 2: FPGA Gateware

**File**: `awto-cynthion/cynthion/python/src/gateware/facedancer/top.py`

Modify to implement UART slave on PA08/PA09:

```python
# Current: Uses FPGA_ADV pulse train via advertiser
# advertiser = advertiser.Peripheral(pad=advertiser_provider.pins)

# New: Add UART slave peripheral
self.fpga_uart = UartSlavePeripheral(
    tx_pin=moondancer_uart_tx,  # moondancer TX → PA09 to Apollo
    rx_pin=moondancer_uart_rx,   # moondancer RX ← PA08 from Apollo
    baud=115200
)
```

### Phase 3: moondancer Firmware

**File**: `awto-cynthion/firmware/moondancer/src/bin/moondancer.rs`

Replace advertiser pulse-train with UART response handler:

```rust
// OLD: 
// advertiser.control().write(|w| w.enable().bit(true));  // pulse train

// NEW:
fn uart_handler() {
    match uart_recv_byte() {
        0x5A => {  // Heartbeat request
            let status = if watchdog_alive { 0x00 } else { 0xFF };
            uart_send_byte(0x5A);
            uart_send_byte(status);
        }
        // ... other commands ...
    }
}
```

---

## Investigation Questions

### Why Was Pulse-Train Chosen?

1. **Gate count limitation?**  
   - ECP5 has ~85k LEs; plenty of room for UART slave
   - Unclear if gate count was ever a real constraint

2. **Timing concerns?**  
   - UART at 115200 baud is well-supported by FPGA
   - No special timing requirements

3. **Was UART considered and rejected?**  
   - Search: commits mentioning "uart" + "fpga" + "apollo"
   - Check: design docs or decision records

4. **Legacy design debt?**  
   - When was advertiser added vs when were UART pads discovered?
   - Was there an earlier attempt that failed?

### Search Results Needed

```bash
git log --all --oneline --grep="uart.*fpga\|fpga.*uart\|advertis" -i
git log --all -S "SERCOM2" -- "*/uart.c"
git log --all -S "MUX_PA08\|MUX_PA09" -- "*/uart.c"
```

---

## Backward Compatibility

### Breaking Changes

- **Apollo firmware**: UART pins change from PA11/PA14 to PA08/PA09
  - Old debugging via PA11/PA14 UART stops working
  - New UART available on PA08/PA09

- **FPGA gateware**: Advertiser CSR no longer used
  - moondancer firmware no longer writes `advertiser.control()`
  - watchdog signal moves from pulse-train to INT pin (PA03)

- **Python host tools**: No changes needed
  - moondancer still appears as 1d50:615b on TARGET-A USB
  - Apollo still appears as 1d50:615c on CONTROL USB
  - Existing `cynthion` CLI commands work unchanged

### Migration Path

1. New moondancer firmware with UART handler (opt-in via gateware build flag)
2. New Apollo firmware with SERCOM2 PA08/PA09 UART
3. Deprecate advertiser mechanism gradually

---

## Risk Assessment

### Low Risk
- ✅ Uses existing SERCOM2 hardware
- ✅ PA08/PA09 not used for anything critical post-bootup
- ✅ FPGA gateware changes are additive (don't remove advertiser immediately)

### Medium Risk
- ⚠️ UART timing tolerance (need testing at various baud rates)
- ⚠️ EMI on PA08/PA09 (runs alongside other signals)

### High Risk
- ❌ If PA08/PA09 have hardware issues not documented
- ❌ If UART slave CSR implementation has bugs

---

## References

- Issue #15: firmware+gateware: use Apollo ARM supervisor for watchdog
- ATSAMD11D14A Datasheet: SERCOM2 UART pad options
- ECP5 GPIO: PA08/PA09 I/O characteristics
- Current advertiser implementation: `apollo_fpga.gateware.advertiser`

---

## Related Code

**Files to investigate:**
- `awto-cynthion/firmware/moondancer/src/bin/moondancer.rs:126-128` - advertiser control
- `awto-apollo/firmware/src/boards/cynthion_d11/uart.c:34-43` - current UART pinmux
- `awto-apollo/firmware/src/boards/cynthion_d11/fpga_adv.c` - pulse-train handler

**Files to modify:**
- `awto-apollo/firmware/src/boards/cynthion_d11/uart.c` - new PA08/PA09 pinmux
- `awto-apollo/firmware/src/boards/cynthion_d11/fpga_adv.c` - INT pin handler (replace pulse counting)
- `awto-cynthion/cynthion/python/src/gateware/facedancer/top.py` - add UART slave
- `awto-cynthion/firmware/moondancer/src/bin/moondancer.rs` - UART response handler

---

## Questions for Design Review

1. Has a UART-based design been attempted before?
2. Why does Apollo do so little (just JTAG + FPGA loading)?  
   - Could Apollo's role expand to watchdog/diagnostics?
3. Is out-of-band signaling (INT pin) the right approach, or should all data go over UART?
4. Should heartbeat be host-driven (Apollo→moondancer) or moondancer-driven?

