# Retired docs

Superseded documents kept for the reasoning they contain, not as current
guidance. Nothing here describes how the code works today.

## apollo_moondancer_uart_watchdog_design.md, apollo_moondancer_uart_watchdog_workstream.md

Retired 2026-07-28. Proposed an Apollo↔moondancer watchdog over a UART moved to
PA08/PA09, to free the JTAG pins.

Dead for two independent reasons:

- **The relocation is impossible on d11.** PA08 is `FPGA_PROGRAM` and PA09 is
  `PHY_RESET` + `FPGA_ADV`; moving the UART there would cost Apollo the ability
  to reset and detect the FPGA. See
  [#65](https://github.com/awtoau/cynthion-workspace/issues/65). The design doc
  carries a correction banner saying so, but its own replacement proposal (SPI on
  SERCOM2, [#62](https://github.com/awtoau/cynthion-workspace/issues/62)) was
  also not what happened.
- **The problem it solved is solved.** Apollo↔FPGA runtime communication is the
  half-duplex sideband on the existing FPGA_ADV wire, documented in
  [`docs/apollo_samd11_mcu/fpga-adv-sideband.md`](../../docs/apollo_samd11_mcu/fpga-adv-sideband.md)
  and shipping. No pin relocation, no board change.

Kept because the supervision model and failure-mode analysis were worked out
here and are not recoverable from the code. Closes the duplication complaint in
[#57](https://github.com/awtoau/cynthion-workspace/issues/57).
