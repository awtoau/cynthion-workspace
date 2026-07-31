# Board register dump: every identification value read from r1.4 silicon

Everything this workspace has read directly out of the parts on Cynthion r1.4, in one
place, with the script that produced each value. **Measured, not transcribed from
datasheets** — where a datasheet disagrees, that is noted, because the disagreements were
the interesting part.

Board: Cynthion r1.4. Values are from one board; a second has not been checked.

## ECP5 FPGA — LFE5U-12F marking, 25F die

| what | value | how |
|---|---|---|
| IDCODE | `0x21111043` | `apollo jtag-scan` |
| part reported | `LFE5U-12F` | same |
| LUT4s advertised | 12,288 | datasheet for a 12F |
| **LUT4s placed and verified** | **20,143** | `ecp5-test/fabric/FABRIC_TEST.md` |
| available on the die | 24,288 | a 25F |
| timing | 86.43 MHz against a 60 MHz constraint | nextpnr |
| correctness | 22,026 rounds, **zero** mismatches | fabric test, both runs |
| control | 1,575 of 1,575 failed, sticky flag set | `0xdeadbeef` injected |

**The part marked 12F is a 25F die.** 7,855 LUT4s beyond the marking placed, routed, met
timing and computed a correct signature, with logic spread across every logic row. The
control matters: the detector is demonstrated to fire on wrong answers, so zero mismatches
is a real negative rather than a broken test. 12F and 25F share a speed grade, so the
timing margin is genuine rather than a gently-clocked design.

**Not established:** intermittent defects. This is one part, and binning for occasional
wrongness is not excluded by a passing run.

**Block RAM is taken as working** without an equivalent test. nextpnr reports 56 DP16KD —
the 25F figure rather than the 12F's 28 — and every build places 41 of them into the CPU's
caches, 64 KiB of program memory and the console FIFO. The CPU fetches from block RAM,
executes, and computes correctly while the FIFO carries characters uncorrupted. That is
ordinary use rather than a dedicated walk, but marginal block RAM surfaces as garbage
instructions or dropped bytes, not as something subtle.

## Configuration flash — Winbond W25Q32

| register | value | meaning |
|---|---|---|
| JEDEC ID | `EF 40 16` | Winbond, type 0x40, capacity code 0x16 |
| capacity from ID | 4 MiB | 2^22 |
| **SFDP density** | **4 MiB** | the die's own declaration, independent of the ID byte |
| unique ID | `355027cba3ac60de` | `apollo flash-info` |
| reads at 4, 8, 12 MiB | all alias offset 0 | `scripts/flash_capacity_probe.py` |
| status register 3 | `0x60`, ADS clear | 4-byte addressing absent |
| reads past 16 MiB | no response | as expected |

**Exactly 4 MiB, confirmed three ways.** The aliasing test is sound because offset 0 holds
real bitstream data — `Part: LFE5U-12` is legible in the hex — rather than erased `0xFF`.
Two blank regions would match trivially and prove nothing; that trap is why the probe
compares against live data.

Detail and speed characterisation: `luna_ecp5_fpga/flash-detailed.md`.

## HyperRAM — Winbond W956A8MBYA6I

Named in `repos/cynthion/.../gateware/facedancer/top.py:42`. **Nothing in this workspace
recorded what this chip was** before #109 — throughput had been characterised in detail
without anyone naming the manufacturer or the density.

### Identification and configuration registers

| address | register | value | decode |
|---|---|---|---|
| `0x0000` | ID0 | `0x0c86` | see below |
| `0x0001` | ID1 | `0x0001` | die revision |
| `0x0800` | CR0 | `0x8f2f` | normal operation, latency 2, fixed latency, wrapped burst |
| `0x0801` | CR1 | `0xffc1` | distributed refresh controls |

**ID0 `0x0c86` decoded**, against `sources/Winbond-W956A8MBYA-64Mbit-HyperRAM.pdf`:

| bits | field | raw | meaning |
|---|---|---|---|
| 15:14 | die address | `00` | die 0 |
| 12:8 | row address bits | `01100` = 12 | **13 bits** — 8192 rows |
| 7:4 | column address bits | `1000` = 8 | **9 bits** — 512 columns |
| 3:0 | manufacturer | `0110` | Winbond, per its table 8 |

**Both count fields are minus-one** — table 5.2 gives `00000` as *"One Row address bit"*.
So 8192 × 512 × 2 = **8 MiB**, and section 8.1.1 states it outright: *"9 column and 13 row
address bits ... 2^22 = 4M words = 8M bytes"*.

**64 Mbit is 8 MiB.** Misreading that as 4 MiB is what made an ordinary part look like it
held twice its marking, and produced three successive wrong explanations before the
datasheet settled it. See `luna_ecp5_fpga/hyperram-detailed.md`.

### Manufacturer Information Register — undocumented in the HyperBus spec

HyperBus specifies four registers. Winbond adds a fifth at `0x1000`, named in section 9.1
table 5 as *"Manufacturer Information Register (0~17) read"*, **read only**, spanning
`0x1000`–`0x1011`.

| address | value | ASCII (LE) |
|---|---|---|
| `0x1000` | `0x3030` | `00` |
| `0x1001` | `0x3230` | `02` |
| `0x1002` | `0x3739` | `97` |
| `0x1003` | `0x3034` | `40` |
| `0x1004` | `0x0736` | — |
| `0x1005` | `0x4c8d` | — |
| `0x1006` | `0x3320` | reserved per table 6 |
| `0x1007` | `0x3320` | reserved per table 6 |
| `0x1008`–`0x100b` | repeats `0x1000`–`0x1003` | block is 8 addresses wide |

First eight bytes little-endian read **`00029740`** — a lot or date code.

**The register is named but not defined.** Section 9 details ID0/ID1, CR0 and CR1 and
stops, so that reading is plausible rather than vendor-confirmed. **Ten of its eighteen
words have never been read** — the sweep stopped at `0x100b`.

Four artifacts excluded before believing it: not the dead-bus pattern (`0x8484`, which is
what memory above 8 MiB and the whole top-die register space return), not a mirror of a
documented register (`0x2`/`0x4`/`0x400` return ID0's value and `0x802`/`0xc00` return
CR0's — the address decode is incomplete), not the memory array (stamping memory at word
`0x1000` with `0xDEAD` left the register reading `0x3030`), and not bitstream bleed.

**It refuses writes**, with a control proving the write path: writing `0x5a5a` left it at
`0x3030` while a CR0 write in the same run read back changed.

### Measured behaviour

| | |
|---|---|
| streaming throughput | 220.2 MB/s, 92.8% of theoretical |
| verified clock ceiling | 120 MHz |
| address space | flat linear 0–8 MiB, no die-select handling needed |

## SAMD11 — Apollo

| | |
|---|---|
| firmware | `v1.1.1-58-g6520707` |
| USB ID | `1d50:615c` (debugger and Saturn-V bootloader) |
| flash | 13,608 / 14,336 bytes, 94.92% |
| RAM | 3,472 / 4,096 bytes, 84.77% |

## Reading these yourself

| script | what it dumps |
|---|---|
| `scripts/flash_capacity_probe.py` | flash JEDEC, SFDP, aliasing — read-only |
| `scripts/hyperram_identify.py` | HyperRAM ID0/ID1/CR0/CR1 and bank aliasing |
| `scripts/hyperram_regfuzz.py` | the `0x1000` register block, plus a write test |
| `apollo jtag-scan` | ECP5 IDCODE |
| `apollo flash-info` | flash JEDEC and unique ID |

## What is still unread

- **Ten of the MIR's eighteen words**, `0x100c`–`0x1011`. Queued for the Rust CLI (#109):
  every read here cost a gateware build, a flash and a JTAG read, which is why the sweep
  stopped where it did. A CPU on the bus reads all eighteen in microseconds.
- **Block RAM has not been walked** the way the fabric was — the fabric test proved LUT4s
  only. It is nonetheless **taken as working**, on the strength of ordinary use rather
  than a dedicated test: nextpnr reports 56 DP16KD (the 25F figure, not the 12F's 28),
  every build places 41 of them, and those blocks carry the CPU's I-cache, D-cache, 64 KiB
  of program memory and the console FIFO. The CPU fetches from block RAM, executes, and
  computes `0x12345678 * 3` correctly while the FIFO delivers characters uncorrupted.
  Marginal block RAM would surface as garbage instructions or dropped bytes well before
  anything subtle. Worth revisiting only if a fault appears that smells like memory
  corruption.
- **Flash writes and erases.** Everything above is reads; write and erase timing has never
  been measured (#93).
