# Chip docs: the Performance section

Headline of each part's doc: ceiling, distance below it, what closes the gap. `## Performance` goes immediately after the intro, before registers, wiring or history.

## Structure

1. **Theoretical maximum.** Datasheet page/section cited; arithmetic shown so it can be re-derived.
2. **Achievable on this board.** Theoretical minus what hardware forces (pins, traces, package, supply, FPGA limits). Name the binding constraint; the gap is a board fact, not a defect.
   - The binding constraint is often a PIN. Check for:
     - driving primitive (ECP5 config flash clock only from `USRMCLK`, which has its own ceiling and is not general IO);
     - pin group (DQS capture needs data lines and strobe in one DQS group);
     - single-ended vs differential;
     - pin shared three ways (arbitration sets the rate);
     - IO standard / drive strength (bitstream attributes, bound edge rate);
     - hard DDR register vs fabric.
   - If a pin binds: which pin, which primitive, what the part would do if wired differently (tells whether a board revision is worth it).
3. **Measured.** Conditions attached: clock, mode, width, burst length, driver. No trustworthy measurement: one line saying so, not a figure of unknown provenance (cost: `chips/hyperram/bist-plan.md`).
4. **The gap, and what closes it.** Ranked with estimates. "Unknown" is legitimate; a guess dressed as an estimate is not.

Every section ends with:

| path | theoretical | board max | measured | % of board max | what closes the gap |
|---|---|---|---|---|---|

## Axes per part

| part | axes that must appear |
|---|---|
| W956A8 HyperRAM | read vs write; non-DQS vs DQS; burst length vs tCSM; CK; latency mode |
| W25Q32 flash | 1-lane vs quad; read vs page program vs erase; continuous-read; SCK; host->flash programming (different path from CPU reads, 30x slower than the chip) |
| ECP5 LFE5U-12F/25F | fmax by design; block RAM; LUT; DDR pin rate; PLL range; what the 25F die gives over the 12F marking |
| VexiiRiscv | IPC; CPI; cache hit/miss cost; fetch vs data stalls (perf counters exist) |
| SAMD11 / Apollo | JTAG shift rate; USB round-trip cost; flash budget; what bounds the programming loop |
| USB3343 ULPI | FS/LS/HS line rates; ULPI byte rate at 60 MHz; what the FPGA side can absorb |
| PAC1954 | sample rate; resolution as configured, not as specified |
| FUSB302B | I2C rate; PD message turnaround |
| NS16550A console | baud ceiling; bytes/s through the USB path behind it |

## Resolution is part of performance

- An ADC on the wrong range throws away bits and nothing reports it. PAC1954: a 0-31 V range measuring a 5 V rail spends most codes on voltages that cannot occur; same for current.
- Select the range from the expected value; re-select when the measured value no longer suits it.
- Glitch/transient measurement is resolution-bound: a dip below one code is invisible, and reads as "did not happen".
- Any measuring peripheral: state resolution as configured, and whether anything selects the range.

## Rules

- Cite everything: datasheet section/page for theoretical; script or commit for measured.
- Conditions or it does not count.
- "Never measured" is a valid entry; a blank is not.
- Do not restate a figure whose provenance cannot be established (several throughput numbers were deleted for this).
