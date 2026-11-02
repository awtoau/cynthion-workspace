# Apollo firmware review: open items

Apollo = ATSAMD11D14A (4 KiB SRAM, 16 KiB flash), bare metal. Pin/ISR facts: [../chips/samd11-apollo.md](../chips/samd11-apollo.md). Flash/RAM budget: same file, § Memory budget.

## DFU ([#53](https://github.com/awtoau/cynthion-workspace/issues/53))

- Descriptor `TUD_DFU_RT_DESCRIPTOR(ITF_NUM_DFU_RT, 0, 0x0d, 500, 4096)` in `mcu/samd11/usb_descriptors.c:104` (and `mcu/rp2040/usb_descriptors.c:107`): 500 = detach timeout, 4096 = advertised transfer size. DFU runtime moves no data, so no 4 KiB buffer is allocated (the earlier "4 KiB of 4 KiB SRAM" claim was wrong). `CFG_TUD_DFU_RUNTIME 1` in `boards/qtpy/tusb_config.h:70`.
- Open: detach/reboot sequencing is timing-dependent with no explicit synchronisation; DFU timeout and interrupted-transfer recovery are undocumented.

## Races

None: bare metal, only an ISR can preempt ([#61](https://github.com/awtoau/cynthion-workspace/issues/61)); what preempts what is in [../chips/samd11-apollo.md](../chips/samd11-apollo.md).

- Open from #61: `src/debug_spi.c:138` releases the UART pinmux on every board revision; the `r0.2+` TODO above it was never answered. Decide whether `uart_release_pinmux()` runs on r0.2+, then delete the TODO.

## Dual CDC ([#56](https://github.com/awtoau/cynthion-workspace/issues/56), closed, deferred)

- Descriptor today: 1x CDC (UART console bridge) + 1x DFU runtime (`CONFIG_TOTAL_LEN = TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + TUD_DFU_RT_DESC_LEN`); `CFG_TUD_CDC 1`. Console and vendor requests share one USB pipe; FPGA debug is JTAG-indirect only.
- A second CDC cannot be a second UART bridge on d11 (one UART-capable pin pair, and it is JTAG's). Role left open: pinless status/control side-channel, see [apollo_serial_interface_and_mode_exclusivity_design.md](apollo_serial_interface_and_mode_exclusivity_design.md).
- If built: second CDC descriptor + IN/OUT endpoints, `tusb_config.h` entry, buffer check under dual load (~1-2 KB RAM).

## Code-quality TODOs

| file | item | severity |
|---|---|---|
| `src/boards/cynthion_d11/uart.c` | UART parity / stop bits unsupported | low |
| `src/boards/daisho/uart.c` | `FIXME: implement all of this!` | low |
| `src/boards/daisho/platform_jtag.h` | `TODO: optimize these?` | low |
| `src/debug_spi.c` | `TODO: don't run on r0.2+?` (see Races) | medium |
| `src/boards/qtpy/apollo_board.h` | RGB neopixel unhandled | low |
| `src/boards/cynthion_d21/spi.c` | `TODO: use FIFO to bulk send` | low |
