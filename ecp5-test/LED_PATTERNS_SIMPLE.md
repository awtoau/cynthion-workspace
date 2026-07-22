# Simple On-Board LED Patterns - Hello World

**Goal**: Generate LED patterns directly on Cynthion FPGA, no USB needed.

---

## Patterns Available

Choose one to build:

| Pattern | File | Description | Speed |
|---------|------|-------------|-------|
| **Chase** | `LEDChase` | Single LED rotates through all 6 | ~1 LED/0.5s |
| **Pulse** | `LEDPulse` | All LEDs fade in/out with PWM | ~3.6 kHz PWM |
| **Strobe** | `LEDStrobe` | All LEDs flash on/off | ~1 Hz |
| **Rainbow** | `LEDRainbow` | Rotating alternating pattern (0b010101/0b101010) | ~1s rotation |
| **All 4** | `LEDPatternGenerator` | Cycles through all patterns (JTAG selectable) | Configurable |

---

## Step 1: Select Pattern

Edit `led_patterns_simple.py`, last line:

```python
# Current: uses LEDChase
top_level_cli(LEDChase)

# Change to any of:
# top_level_cli(LEDPulse)        # All LEDs fade in/out
# top_level_cli(LEDStrobe)       # All LEDs flash
# top_level_cli(LEDRainbow)      # Alternating pattern rotate
# top_level_cli(LEDPatternGenerator)  # All 4 patterns (JTAG select)
```

---

## Step 2: Build Gateware

```bash
cd /mnt/2tb/git/cynthion-workspace

# Generate Verilog RTL
python3 led_patterns_simple.py generate -t rtlil -o led_patterns.il

# Full build (RTL + Synthesis + Place & Route + Bitstream)
python3 led_patterns_simple.py build
```

This produces: `led_patterns.bit` (ready to program)

---

## Step 3: Program Cynthion

```bash
# Via cynthion-control (Apollo debug firmware)
cynthion-control program led_patterns.bit

# Or via DFU
dfu-util -D led_patterns.bit

# Or via openFPGALoader
openFPGALoader -b cynthion led_patterns.bit
```

---

## Step 4: Observe

Plug in Cynthion and watch the 6 LEDs run the selected pattern!

---

## Pattern Details

### Chase (Single LED Rotation)

```
Time:  0.00s → 0.50s → 1.00s → 1.50s
LED 1:  ON  →  OFF  →  OFF  →  OFF
LED 2:  OFF →  ON   →  OFF  →  OFF
LED 3:  OFF →  OFF  →  ON   →  OFF
LED 4:  OFF →  OFF  →  OFF  →  ON
LED 5:  OFF →  OFF  →  OFF  →  OFF
LED 6:  OFF →  OFF  →  OFF  →  OFF
```

**Timing**: `counter[23:26]` changes every ~2.6M cycles at 60 MHz ≈ 0.56 seconds per LED

### Pulse (PWM Fading)

```
Brightness:  0% → 50% → 100% → 50% → 0% (repeats)
All LEDs:   OFF → DIM →  ON  → DIM → OFF
PWM Freq:   ~3.6 kHz (imperceptible flicker)
```

**Timing**: `counter[16:20]` creates 4-bit PWM counter (0-15), `counter[20]` selects fade direction

### Strobe (Flash)

```
Time:    0.0s → 0.5s → 1.0s → 1.5s
All LEDs: ON  → OFF  →  ON  → OFF
```

**Timing**: Bit 25 of counter toggles every ~0.56 seconds

### Rainbow (Alternating Rotate)

```
Time:  0.0s → 1.0s → 2.0s → 3.0s
State: 0b010101 → 0b101010 → 0b010101 → 0b101010
LEDs:  1,3,5 ON → 2,4,6 ON → 1,3,5 ON → 2,4,6 ON
```

**Timing**: Bit 24 of counter determines which pattern (toggles every ~1.12 seconds)

---

## All 4 Patterns (LEDPatternGenerator)

Use `LEDPatternGenerator` to cycle through all patterns:

```python
top_level_cli(LEDPatternGenerator)
```

This runs:
- Pattern 0: Chase (default on boot)
- Pattern 1: Pulse
- Pattern 2: Strobe
- Pattern 3: Rainbow

To change pattern at runtime, add JTAG control (see below).

---

## Adding JTAG Pattern Selection (Optional)

To switch patterns at runtime via JTAG without reprogramming:

```python
class LEDPatternGeneratorWithJTAG(Elaboratable):
    def elaborate(self, platform):
        m = Module()
        
        # ... existing code ...
        
        # Add JTAG interface for pattern_mode control
        jtag = platform.request("jtag", 0, allow_extra_ports=True)
        
        # JTAG can write to pattern_mode (0-3)
        # Read from JTAG gives current pattern
        
        # ... rest of code ...
```

Then control with `openocd` or `urjtag`.

---

## Timing Reference

**60 MHz Clock**, Counter = Signal(26):

| Bits | Period | Frequency | Use |
|------|--------|-----------|-----|
| [15:0] | 65 ns | 15.3 MHz | Fast PWM |
| [19:16] | 1.04 µs | 960 kHz | PWM brightness sweep |
| [23:20] | 16.7 µs | 60 kHz | LED animation |
| [24] | 33.5 ms | 29.8 Hz | Slow flicker |
| [25] | 67 ms | 14.9 Hz | Strobe/flash |
| [26] | 134 ms | 7.5 Hz | Very slow sweep |

---

## Extending the Code

### Add User Button to Control Pattern

```python
button = platform.request("button_user").i

with m.If(button):  # Button pressed
    m.d.sync += pattern_mode.eq(pattern_mode + 1)
```

### Add Brightness Control via Potentiometer

```python
# Requires ADC on PMOD
adc_out = Signal(10)  # 10-bit ADC value
brightness = adc_out[5:10]  # Scale to 0-31

pulse_pattern = Repl(pwm_counter < brightness, 6)
```

### Create Custom Pattern

```python
class LEDCustom(Elaboratable):
    def elaborate(self, platform):
        leds = Cat(platform.request("led", i).o for i in range(6))
        counter = Signal(26)
        m.d.sync += counter.eq(counter + 1)
        
        # Your pattern logic here
        pattern = Signal(6)
        # ... define pattern based on counter ...
        
        m.d.comb += leds.eq(pattern)
        return m
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| LEDs don't light up | Check polarity (active-low), inspect pinout in platform file |
| Pattern too fast/slow | Adjust counter bits (e.g., `counter[22:25]` instead of `[23:26]`) |
| All LEDs always on | Check `leds.eq()` logic and Repl() usage |
| Build fails | Ensure Amaranth + LUNA installed: `pip install amaranth luna` |

---

## Resource Usage

Typical gateware size:

| Pattern | LUTs | Logic | Notes |
|---------|------|-------|-------|
| Chase | ~50 | Small counter + mux | Minimal |
| Pulse | ~60 | Counter + comparator | Minimal |
| Strobe | ~40 | Single bit select | Minimal |
| All 4 | ~80 | Counter + 4-way mux | Still very small |

**ECP5-12F total**: 12,288 LUTs → all patterns use < 1% of resources

---

## Next Steps

1. ✅ Build one pattern, verify LEDs work
2. ✅ Combine multiple patterns, select via button
3. ✅ Add JTAG control for runtime selection
4. ✅ Integrate with HyperRAM test (LED status indicator)
5. ✅ Use LEDs as test/debug output for CPU

---

## Files

- `led_patterns_simple.py` - Main source (Amaranth HDL)
- `led_patterns.bit` - Generated bitstream (after build)
- `LED_PATTERN_HELLO_WORLD_SIMPLE.md` - This guide
