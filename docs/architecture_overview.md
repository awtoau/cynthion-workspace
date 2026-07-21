## Architecture

### Repository Structure
```
$HOME/git/awtoau/
├── awto-apollo/              # Apollo debug controller (ARM)
│   └── firmware/
│       └── Makefile          # make APOLLO_BOARD=cynthion
├── awto-cynthion/            # Gateware + moondancer
│   ├── cynthion/python/      # Gateware (Amaranth)
│   └── firmware/
│       └── moondancer/       # RISC-V firmware (Rust)
├── awto-luna/                # Luna USB framework
└── awto-facedancer/          # Facedancer USB device
```

### Build Flow
```
Repos (GitHub)
  ↓
install.py setup
  ├─→ Clone all repos
  ├─→ Init submodules (TinyUSB, etc)
  ├─→ Install Python deps (Amaranth, etc)
  │
  ├─→ Apollo firmware
  │   └─→ make APOLLO_BOARD=cynthion
  │       └─→ firmware/build/cynthion_d11/apollo_debug_soc.elf
  │
  ├─→ moondancer firmware
  │   └─→ cargo build --release
  │       └─→ target/riscv32imac-unknown-none-elf/release/moondancer
  │
  ├─→ Analyzer gateware
  │   └─→ Amaranth elaborate → Yosys → nextpnr → trellis
  │       └─→ bitstream.bit
  │
  └─→ Facedancer gateware
      └─→ Amaranth elaborate → Yosys → nextpnr → trellis
          └─→ bitstream.bit
```

---

