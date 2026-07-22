# LED Pattern Hello World - Build & Run Guide

**Goal**: Control Cynthion's 6 LEDs via USB using gateware + Python host script.

## What You'll Learn

✅ How to write LUNA gateware (Amaranth HDL)  
✅ How to handle USB vendor requests  
✅ How to interface with platform resources (LEDs, buttons)  
✅ How to control hardware from Python via USB  

---

## Files

| File | Purpose |
|------|---------|
| `led_pattern_gateware_hello_world.py` | Amaranth HDL gateware (build into bitstream) |
| `led_pattern_hello_world.py` | Python host script (controls LEDs via USB) |

---

## Step 1: Build Gateware

### Option A: Using LUNA CLI (Recommended)

```bash
cd /mnt/2tb/git/cynthion-workspace

# Generate Verilog RTL from Amaranth
python3 led_pattern_gateware_hello_world.py generate -t rtlil -o led_pattern.il

# Synthesize with Yosys + Place & Route with nextpnr
python3 led_pattern_gateware_hello_world.py build

# This produces: led_pattern.bit (bitstream)
```

### Option B: Step-by-Step (Manual)

```bash
# 1. Generate Verilog
yosys -p "read_verilog led_pattern.v; write_json led_pattern.json"

# 2. Synthesize & place & route
nextpnr-ecp5 \
  --json led_pattern.json \
  --textcfg led_pattern.config \
  --12k --speed 8 --freq 60

# 3. Generate bitstream
ecppack led_pattern.config led_pattern.bit
```

---

## Step 2: Program Cynthion

```bash
# Using cynthion-control (requires Apollo debug firmware)
cynthion-control program led_pattern.bit

# Or using dfu-util
dfu-util -D led_pattern.bit
```

---

## Step 3: Run Host Script

```bash
# Install pyusb if needed
pip install pyusb

# Run LED pattern demo
python3 led_pattern_hello_world.py
```

**Expected Output**:
```
======================================================================
Cynthion LED Pattern - Hello World
======================================================================

✓ Found Cynthion: Cynthion Project LED Pattern Hello World

LED Patterns:

1. Single LED sweep (chase pattern)
   LED 1 = 0b000001
   LED 2 = 0b000010
   LED 3 = 0b000100
   LED 4 = 0b001000
   LED 5 = 0b010000
   LED 6 = 0b100000

2. All LEDs off
3. All LEDs on
4. Alternating pattern
   Pattern = 0b010101
   Pattern = 0b101010

5. Growing bar pattern
   LEDs 1-1 = 0b000001
   LEDs 1-2 = 0b000011
   ...
   LEDs 1-6 = 0b111111

6. Shrinking bar pattern
7. Turning all LEDs off

======================================================================
✓ LED pattern demo complete!
======================================================================
```

---

## Architecture

### **Gateware Side** (FPGA)

```
┌─────────────────────────────────────────┐
│         USB Device (LUNA)               │
│  ┌─────────────────────────────────────┐│
│  │   Control Endpoint                  ││
│  │  ┌───────────────────────────────┐ ││
│  │  │ Vendor Request Handler        │ ││
│  │  │  Request 0x01: Set LED Pattern│ ││
│  │  │  Request 0x02: Set LED Mode   │ ││
│  │  │  Request 0x03: Get Button     │ ││
│  │  └───────────────────────────────┘ ││
│  └──────────────┬──────────────────────┘│
│                 │                       │
│         ┌───────▼────────┐              │
│         │ LED Output     │              │
│         │ (6 bits DDR)   │─────→ LEDs   │
│         └────────────────┘              │
└─────────────────────────────────────────┘
```

### **Host Side** (Python)

```python
CynthionLEDController
  ├─ set_led_pattern(0-63)    # Set LED bit pattern
  ├─ set_led_mode(0-2)        # Set animation mode
  └─ get_button_state()       # Read user button

# Example: Chase pattern
for i in range(6):
    pattern = 1 << i  # Single bit
    led_ctrl.set_led_pattern(pattern)
    time.sleep(0.2)
```

---

## USB Protocol

### Vendor Request 0x01: SET_LED_PATTERN

```
bmRequestType: 0x40 (Device→Host, Vendor)
bRequest:      0x01
wValue:        0x0000
wIndex:        0x0000
Data Stage:    [pattern_byte] (6 bits, one per LED)
              Bit 0 = LED 1, Bit 1 = LED 2, ..., Bit 5 = LED 6
```

**Examples**:
- `0x01` = Binary `0b000001` = LED 1 only
- `0x3F` = Binary `0b111111` = All LEDs on
- `0x15` = Binary `0b010101` = Alternating (LEDs 1,3,5)

### Vendor Request 0x02: SET_LED_MODE

```
bmRequestType: 0x40 (Device→Host, Vendor)
bRequest:      0x02
wValue:        0x0000
wIndex:        0x0000
Data Stage:    [mode_byte]
              0 = Static (no animation)
              1 = Chase (not implemented yet)
              2 = Pulse (not implemented yet)
```

### Vendor Request 0x03: GET_USER_BUTTON

```
bmRequestType: 0xC0 (Host→Device, Vendor)
bRequest:      0x03
wValue:        0x0000
wIndex:        0x0000
Data Stage:    [button_state] (1 byte)
              1 = Button pressed
              0 = Button released
```

---

## Extending the Example

### Add PWM Brightness Control

```python
# In gateware:
brightness = Signal(4)  # 0-15
led_pwm = Signal()

counter = Signal(4)
m.d.sync += counter.eq(counter + 1)
m.d.comb += led_pwm.eq(counter < brightness)
m.d.comb += leds.eq(Repl(led_pwm, 6))  # Replicate to all LEDs
```

### Add Animation Modes (Chase, Pulse)

```python
# In gateware:
led_mode = Signal(2)  # 0=static, 1=chase, 2=pulse
animation_counter = Signal(25)

with m.Switch(led_mode):
    with m.Case(0):  # Static
        m.d.comb += leds.eq(led_pattern)
    
    with m.Case(1):  # Chase
        m.d.sync += animation_counter.eq(animation_counter + 1)
        chase_idx = animation_counter[22:25]
        m.d.comb += leds.eq(1 << chase_idx)
    
    with m.Case(2):  # Pulse (PWM)
        pwm_counter = animation_counter[0:4]
        m.d.comb += leds.eq(Repl(pwm_counter < 8, 6))
```

### Add Python Animation Loop

```python
# In host script:
def animated_chase(led_ctrl, speed=0.1):
    """Run chase animation on host side."""
    while True:
        for i in range(6):
            pattern = 1 << i
            led_ctrl.set_led_pattern(pattern)
            time.sleep(speed)

# Run it:
animated_chase(led_ctrl, speed=0.15)
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Device not found" | Gateware not programmed, or wrong VID:PID |
| "Permission denied" | Run with `sudo`, or add udev rule |
| LEDs don't change | Check USB vendor request handling, inspect VCD waveform |
| Synthesis fails | Ensure LUNA and Amaranth are installed in venv |

---

## Reference

- **LUNA Documentation**: https://github.com/greatscottgadgets/luna
- **Cynthion Platform**: https://github.com/greatscottgadgets/cynthion
- **USB Specification**: http://www.usb.org/developers/docs/
- **Amaranth HDL**: https://github.com/amaranth-lang/amaranth
