# HyperRAM Burst Test - Minimal Validation

**Goal**: Validate LUNA's HyperRAMInterface works at 60 MHz on Cynthion r1.4 before integrating the Lattice AXI4 controller.

## What This Tests

| Phase | Operation | Purpose |
|-------|-----------|---------|
| 1 | Single-word write (0xDEAD → addr 0x0000) | Basic write |
| 2 | Single-word read (addr 0x0000 → verify 0xDEAD) | Basic read + verification |
| 3 | 4-word burst write (0x1111-0x3333 → addr 0x1000) | Burst write |
| 4 | 4-word burst read (addr 0x1000 → verify data) | Burst read + verification |

## Test Hardware

- **FPGA**: Lattice ECP5-12F (Cynthion r1.4)
- **RAM**: HyperRAM on Cynthion, Cynthion dedicated I/O
- **Clock**: 60 MHz (proven safe timing)
- **Interface**: LUNA `HyperRAMPHY` + `HyperRAMInterface` (non-DQS, current production code)

## Build Steps

### Option A: Full Synthesis (on selwyn with oss-cad-suite)

```bash
cd ecp5-test/hyperram/

# Generate RTL
python3 hyperram_burst_test.py generate -t rtlil -o hyperram_burst_test.il

# Convert to JSON for nextpnr
yosys -m ghdl -p "read_verilog hyperram_burst_test.v; write_json hyperram_burst_test.json"

# Place & route
nextpnr-ecp5 --json hyperram_burst_test.json \
  --textcfg hyperram_burst_test.config \
  --12k --speed 8 --freq 60

# Generate bitstream
ecppack hyperram_burst_test.config hyperram_burst_test.bit
```

### Option B: Quick RTL Check (no hardware build)

```bash
python3 hyperram_burst_test.py generate -t rtlil -o hyperram_burst_test.il
# Inspect RTL for syntax errors, state machine logic
```

## Deployment to Cynthion

```bash
# Via cynthion-control (requires Apollo debug firmware)
cynthion-control program hyperram_burst_test.bit

# Or via direct DFU if available
dfu-util -D hyperram_burst_test.bit
```

## Verification

After programming:

1. **Monitor JTAG registers** for `test_passed` / `test_failed` signals
2. **Inspect Cynthion analyzer** for HyperRAM bus traffic (if enabled)
3. **Benchmark**: Measure read/write latency and throughput

Expected results:
- Single-word read/write: ~150-200 ns (latency + transfer time)
- Burst-4 throughput: ~2-3 Gbps (16-bit DDR at 60 MHz)

## Exit Status

| Signal | Meaning |
|--------|---------|
| `test_passed = 1` | All R/W operations successful |
| `test_failed = 1` | Verification failed (data mismatch) |
| Both 0 | Test still running or incomplete |

## Relationship to Linux CPU Integration

✅ **This validates the PHY layer** — if burst test passes, the LUNA HyperRAM interface is reliable at 60 MHz on Cynthion.

🔜 **Next step**: Wrap with Lattice AXI4 controller for CPU memory access.

## Debugging

If test fails:

1. **Check clock domains** — is 60 MHz clock reaching HyperRAM?
2. **Verify pinout** — do platform resources match hardware?
3. **Check termination** — are pull-ups/signal integrity OK?
4. **Try diagnostic** — run `luna/applets/hyperram_diagnostic.py` (more verbose)
5. **Reduce frequency** — try 30 MHz if 60 MHz unstable

## Files

- `hyperram_burst_test.py` — Main test bench (Amaranth)
- `test_hyperram_build.py` — Build script helper
- `README.md` — This file

## References

- [LUNA HyperRAM Interface](https://github.com/greatscottgadgets/luna/blob/main/luna/gateware/interface/psram.py)
- [Cynthion Platform](https://github.com/greatscottgadgets/cynthion)
- [HyperRAM Spec](https://www.infineon.com/cms/en/product/memories/psram/)
