# HyperRAM clean-room baseline (reference, #581)

- **Reference design, not the SoC driver.** Standalone test top: generator -> AsyncFIFO -> engine + `HyperBusCore` (own PLL) -> AsyncFIFO -> checker; registers over LUNA JTAG.
- Written from the W956A8 datasheet (`~/vendors/winbond/W956x8MBYA_A01-006.pdf`), the HyperBus spec and public cores only; board facts (pinout, 60 MHz oscillator, bank voltage) from GSG's public r1.4 platform. No repo controller code.
- What it is for:
  - the known-good measurement the SoC peripheral (#588) is compared against: same core, same pins, same CK
  - re-running the CK sweep / read eye on a board: `scripts/hyperram/hyperram_baseline.py sweep`
  - the datasheet model and negative controls: `scripts/hyperram/hyperram_baseline_sim.py`
- Figures and conditions: `docs/chips/hyperram/sequential-baseline.md`.
- The SoC peripheral reuses `hyperbus.py` (`HyperBusCore`, `ECP5HyperBusPHY`, `max_burst_words`) and the PLL helpers in `top.py`; changes to them must keep `hyperram_baseline_sim.py` passing.
