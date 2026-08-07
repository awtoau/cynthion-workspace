# Every chip doc gets a Performance section

The project is past "does it work". The question now is **what is the ceiling,
how far below it are we, and what closes the gap** — so that has to be the
headline of each part's document, not a footnote.

## The shape

A `## Performance` section, placed **immediately after the intro**, before
registers, wiring or history. Same structure in every chip doc:

### 1. Theoretical maximum

From the datasheet, with the page or section cited. Derived arithmetic shown, not
just the answer — a number nobody can re-derive is a number nobody can check.

### 2. Achievable on this board

The theoretical figure minus what this hardware forces: pin count, trace length,
package, supply, the FPGA's own limits. Say which constraint binds and why. This
is usually well below (1) and the gap is a board fact, not a defect.

### 3. Measured

What we actually get, with the conditions attached: clock, mode, width, burst
length, and what was driving it. **A measurement without conditions is not a
measurement.**

If nothing trustworthy has been measured, say so in one line. Do not fill the row
with a figure of unknown provenance — see `chips/hyperram/bist-plan.md` for what
that costs.

### 4. The gap, and what closes it

Ranked, with an estimate of what each is worth. "Unknown" is a legitimate entry;
a guess dressed as an estimate is not.

## The table every section ends with

| path | theoretical | board max | measured | % of board max | what closes the gap |
|---|---|---|---|---|---|

## Per-part axes to cover

Each part has its own dimensions. Cover them all — a single headline figure
hides the axis that matters.

| part | axes that must appear |
|---|---|
| **W956A8 HyperRAM** | read vs write; non-DQS vs DQS; burst length against tCSM; CK; latency mode |
| **W25Q32 flash** | 1-lane SPI vs quad; read vs page program vs erase; continuous-read mode; SCK; **host→flash programming, which is a different path from CPU reads and is 30× slower than the chip** |
| **ECP5 LFE5U-12F/25F** | fmax by design, not one number; block RAM; LUT; DDR pin rate; PLL range; what the 25F die gives over the 12F marking |
| **VexiiRiscv** | IPC; cycles per instruction; cache hit and miss cost; fetch vs data stalls (the perf counters exist — use them) |
| **SAMD11 / Apollo** | JTAG shift rate; USB round-trip cost; flash budget; what bounds the programming loop |
| **USB3343 ULPI** | FS/LS/HS line rates; ULPI byte rate at 60 MHz; what the FPGA side can absorb |
| **PAC1954 power monitor** | sample rate; **resolution as configured, not as specified** — see below |
| **FUSB302B** | I²C rate; PD message turnaround |
| **NS16550A console** | baud ceiling; bytes/s through the USB path behind it |

## Resolution is part of performance

Flagged by Dan and it generalises: an ADC configured on the wrong range throws
away bits, and nothing reports it.

The PAC1954 is the named case — a **0–31 V** range used to measure a **5 V**
rail spends most of its codes on voltages that cannot occur. Same for current.
The fix is to select the range from the expected value, and to re-select
automatically when the measured value no longer suits the range.

This matters beyond tidiness: **glitch and transient measurement is resolution-
bound.** A supply dip too small to cross a code boundary is invisible, and
invisible reads identically to "did not happen".

So for any measuring peripheral, the Performance section must state the
resolution **as configured**, not as specified in the datasheet — and whether
anything selects the range.

## Rules

- **Cite everything.** Datasheet section or page for theoretical; a script or
  commit for measured.
- **Conditions or it does not count.** Clock, mode, width, what was driving it.
- **"Never measured" is a valid and useful entry.** A blank is not.
- **Do not restate a figure whose provenance you cannot establish.** Several
  throughput numbers in this tree were deleted for exactly that reason.
