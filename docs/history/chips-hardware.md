# History: hardware / chips docs

Removed from the live docs (superseded, or journey rather than state). Agents do not read this file.

## docs/hardware.md, intro (removed 2026-11-02)

This board's facts have been re-derived — wrongly — more than once: a power monitor
called an INA when its silicon says PAC1954, an FPGA assumed to have 12,288 LUTs
when 20,143 have been placed and verified, a 64 Mbit HyperRAM read as 4 MiB. Each
of those was already written down when it was re-derived. If something here is
wrong, fix it here; do not work around it in a third file.

Bus-speed audit pointer: "Written after the I²C bus turned out to have run twelve times slower than every device on it for the life of the project."

## docs/hardware.md, "Claims that keep coming back wrong" (removed; each fact is stated in its live home)

| Claim | Actual |
|---|---|
| "The power monitor is an INA3221 or similar" | **PAC1954-1.** `PRODUCT_ID` reads `0x7B`, `MANUFACTURER_ID` `0x54`, read from silicon by two independent paths. |
| "The ECP5 has 12,288 LUT4s" | **20,143 have been placed, routed and verified** on a 24,288-LUT die. |
| "The HyperRAM is 4 MiB" (from "64 Mbit") | **8 MiB.** 64 Mbit ÷ 8 = 8 MiB. Three successive wrong explanations came from that one arithmetic slip. |
| "Both FUSB302Bs could share a bus" | They are both at `0x22`. They cannot. |
| "USB VID:PID `1d50:615b` for everything" | Apollo is **`1d50:615c`**; only gateware is `615b`. |
| "The Apollo bootloader is `1d50:60e6`" | Saturn-V uses **`1d50:615c`**, the same ID as Apollo firmware. `60e6` appears nowhere in the tree. |
| "`1d50:615b` means the board is not ready for DFU flashing" | It means gateware is running. A `0xF0` advertiser-stop or `--force-offline` handoff recovers the port. Distinguishing DFU from Apollo firmware needs the **interface descriptor**, not the PID. |
| "Subclass `0x10` = analyzer, `0x20` = moondancer" | Correct but incomplete — it omits **`0x00` = Apollo stub / flash bridge**, which is the subclass the handoff path actually matches on. |
| "At boot Apollo holds the port and the FPGA is in reset" | **Inverted.** Normal boot reconfigures the FPGA and hands it the port. Apollo only holds it when PROGRAM is pressed at boot. |
| "Configuration completing is what makes Apollo cede CONTROL" | Ownership is driven **continuously** by the FPGA_ADV keepalive, and ceding also requires the host to have sent `0xc2`. |
| "Hung firmware needs a power cycle" | See the recovery ladder. `0xec` and `0xed` remain permitted even mid-JTAG-programming. |
| "Apollo has a debug SPI to the FPGA" | The handlers exist but are gated on `_BOARD_HAS_DEBUG_SPI`, which `cynthion_d11` does not define. Those requests STALL. SPI goes via the JTAG ER1/ER2 tunnel. |
| "`spi.c` uses PA08/PA09/PA10" | Its **comment** says that. The code below muxes PA14/PA15/PA10. PA08 is FPGA_PROGRAM and PA09 is FPGA_ADV. |
| "ttyACM0/1/2 — three consoles" | Still a plan. `CFG_TUD_CDC` is **1**, so exactly one CDC interface exists. |

## docs/hardware.md, LED rationale (removed; the table and meanings stay)

Driven so that **a working board and a dead one do not look the same** — nothing
drove them before, which is a large part of why the silent SoC took days.

**The colour order is the schematic's, not the platform's.** `LEDResources` is
positional and carries no colour, so nothing in the gateware or firmware could
contradict a wrong name — and a reversed order survived in three places until
someone watched the board and saw the wrong lamp blinking ([#415](https://github.com/awtoau/cynthion-workspace/issues/415)).

**No `ever_fetched` or `ever_io` lamp.** Both latch within microseconds of any
boot and never distinguish anything again, so the board would carry two
permanently-lit lamps — the dead-instrument problem this section exists to
avoid ([#411](https://github.com/awtoau/cynthion-workspace/issues/411)).

The same correction made the sideband usable — its `state` was raw Wishbone `cyc`, high only during a transaction, so reading `0` was near-certain even on a busy CPU.

## docs/hardware.md, SoC register-offset incident (removed)

That is precisely how firmware came to send `0x9f << 24` on the strength of a comment asserting the PHY did not left-justify, when it does — the hardware and the comment disagreed and nothing could catch it.

## docs/hardware.md, `info` / memory budget paragraphs (superseded: image RAM is now 31 KiB, .text/.rodata in flash; see firmware/cynthion-soc/memory.x)

The shell is an image, not the resident half, and that is what its budget
rests on. `memory.x` puts it at `0x0400` with 63 KiB, above the 492-byte
bootloader that owns the reset vector.

With `board`, `info`, `selftest`, `time` and `stats` all present it measures
`.text` 25,996 + `.rodata` 9,976 + `.bss` 1,460 = 37,432 bytes, leaving **27,048
free** of 64,512. `stats` and the collection behind it account for 1,088 of
that — 772 of code, 260 of format strings and 48 of counters.
Under the previous 32 KiB split the same firmware was over by 3,748 bytes with no
stack at all and did not link. Two reclamations from that period stay because they
were genuine deletions of waste: `.eh_frame`, ~1800 bytes of unwind tables lld was
placing in RAM for a target that cannot unwind (`/DISCARD/` in both linker
scripts), and `opt-level = "z"` in place of `"s"`, another 1612.

Register-reference counts (superseded by the regenerated SVD, now 21 peripherals / 146 registers / 210 fields by element count): "`firmware/cynthion-soc-pac/soc.svd` (CMSIS-SVD: 12 peripherals, 55 registers, 96 fields)"; "`gateware/board/cynthion_r1_4.py` — 206 lines of pin declarations" (now 283 lines).

## docs/hardware.md, `cpu stats` (removed)

**Measured under QEMU: an untouched shell is 0.15% busy.** 99.85% of cycles are
`irq::pop` returning `None`. That is the figure to weigh against RTIC or Zephyr,
with the caveat that TCG is not the board — `mcycle` there advances once per
instruction, so IPC reads 1.000 and means nothing, and the board's cache misses
and Wishbone stalls are absent by construction.

Illustrative sample (none measured on the board):

```
cycles   window 1073741824  busy 0.15%  ipc 0.412
loop     turns 1874233  mean 572 cycles  worst 141202 cycles
poll     every 50 ms  polls 24019  worst gap 61 ms
```

Window rationale: the alternative to halving was a 64-bit divide, which on rv32 is `__udivdi3`: 912 bytes, and the same reason `time` prints a raw `mtime`.

## docs/sbu.md (removed)

"Proved in simulation, not on hardware. ... Both carry negative controls that are *run* — a turnaround one clock late, an inverted parity bit, an undefined acknowledge, a contending target — each asserting the corruption is reported."

"What a board still has to settle: that the 105 MHz domain closes timing" (timing claim dropped from the live doc).
