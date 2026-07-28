# Configuration flash: identification, read modes and speed

The r1.4 configuration flash is a **Winbond W25Q32**, JEDEC ID `EF 40 16`
(manufacturer `EF`, type `40`, capacity `16` = 2^22 = **4 MiB**). Read by the
FPGA itself over SPI, and confirmed independently by `apollo flash-info`, which
reports the same ID plus unique ID `355027cba3ac60de`.

If the FPGA is loaded over USB at startup rather than from flash, the whole
4 MiB is free — see [Using it as RISC-V storage](#using-it-as-risc-v-storage).

## Measured, verified results

Every row was checked by comparing the bytes the FPGA read against the same
region read through `apollo flash-read`, which uses a completely independent
path (JTAG bit-banged by the SAMD11). A pass means two unrelated mechanisms
agree on the data, not that one mechanism is self-consistent.

| SCK | opcode | throughput | data | verdict |
|---|---|---|---|---|
| 15 MHz | `0x03` | 1.87 MB/s | matches | PASS |
| 30 MHz | `0x03` | 3.75 MB/s | matches | **PASS** |
| 30 MHz | `0x0B` | 3.75 MB/s | matches | **PASS** |
| 60 MHz | `0x03` | 7.49 MB/s | all zeros | FAIL |
| 60 MHz | `0x0B` | 7.49 MB/s | all zeros | FAIL |

**30 MHz is the verified working ceiling.** Five reconfigurations there returned
bit-identical cycle counts and CRCs.

### Why 60 MHz fails, and what it is not

Both opcodes fail *identically*. That is the diagnostic: `0x0B` is rated to
104 MHz and `0x03` only to 50 MHz, so if the flash were the limit the fast-read
variant would have survived where the plain read died. It did not, so **the
limit is our sampling path, not the part.**

The failure signature is all zeros — a dead MISO, not corrupted or shifted data.
The round trip (fabric → `USRMCLK` → pad → flash access time → pad → sample)
exceeds one half-period at 60 MHz. Fixing it means moving the sample point, not
changing the opcode: sample on the falling edge, or add a delay, or clock the
input register from a phase-shifted clock.

This was only diagnosable because the design mirrors the first 1 KiB of every
read into ECP5 block RAM and reads it back over JTAG. A checksum alone reports
"wrong" without saying how, and worse, the original XOR fold cancelled on any
even-length run of constant data — so a dead MISO and a healthy read of 4096
zeros both folded to `0x00`. Two conclusions were drawn from that broken
detector before the buffer disproved them; it is now a CRC-8.

## What the part supports

From the datasheet (99 pages, revision J):

| Mode | Opcode | Max clock | Lanes | Ceiling | Available here |
|---|---|---|---|---|---|
| Read | `0x03` | 50 MHz | 1 | 6.25 MB/s | yes, working |
| Fast read | `0x0B` | 104 MHz | 1 | 13 MB/s | yes, working |
| Fast read dual output | `0x3B` | 104 MHz | 2 | 26 MB/s | pins wired, not implemented |
| Fast read quad output | `0x6B` | 104 MHz | 4 | 52 MB/s | pins wired, not implemented |
| Fast read dual I/O | `0xBB` | 104 MHz | 2 | 26 MB/s | pins wired, not implemented |
| Fast read quad I/O | `0xEB` | 104 MHz | 4 | 52 MB/s | pins wired, not implemented |
| Word read quad I/O | `0xE7` | 104 MHz | 4 | 52 MB/s | pins wired, not implemented |

The platform already declares a `qspi_flash` resource with all four data lines
(`T8 T7 M7 N7`), so quad mode needs gateware, not rework.

### Double-edge clocking (DDR)

**Not supported by this part.** The datasheet contains no DTR opcodes — `0Dh`,
`BDh` and `EDh` are absent and the string "DTR" appears nowhere in it. DDR reads
belong to the W25Q-DTR family, which this is not.

The datasheet's own phrasing invites a misreading: it advertises "equivalent
clock rates of 208 MHz (104 MHz × 2) for Dual I/O and 416 MHz (104 MHz × 4) for
Quad I/O". That is **lane parallelism, not double-edge clocking** — the
multiplier comes from using 2 or 4 data lines, each still sampled once per
clock. Quad output delivers the same 4× that DDR-plus-dual would have, and is
actually supported.

So effort is better spent on quad output than on DDR. Note that HyperRAM *is*
a genuine DDR device, so the double-edge work is not wasted — it just belongs
there rather than here.

## Using it as RISC-V storage

Since the bitstream is loaded over USB at startup, the full 4 MiB is available.
For a RISC-V system this is the practical picture:

| Access pattern | Rate today | With quad at 30 MHz | With quad at 50 MHz |
|---|---|---|---|
| Sequential read | 3.75 MB/s | 15 MB/s | 25 MB/s |
| 4 KiB block | ~1.1 ms | ~0.27 ms | ~0.16 ms |
| 256-byte page | ~68 µs | ~17 µs | ~10 µs |

Characteristics that matter for storage rather than for a bitstream:

- **Reads are random-access**, and any address can start a sequential burst that
  runs to the end of the chip. There is no page boundary penalty on reads.
- **Writes are page-bound**: 256 bytes per program operation, 0.7 ms typical
  and 3 ms maximum each.
- **Erase is required before rewriting** and is slow and coarse: 4 KiB sector
  45 ms typical / 400 ms max, 32 KiB block 120 ms / 1600 ms, 64 KiB block
  150 ms / 2000 ms, whole chip 10 s typical / 50 s max.
- **Endurance is more than 100,000 erase/program cycles** per sector with 20-year
  retention, so anything write-heavy needs wear levelling.
- **Continuous Read** with 8/16/32/64-byte wrap is supported, and QPI mode can
  address in as few as 8 clocks — both relevant if this becomes a code store.

This is a good read-mostly filesystem or executable store, and a poor general
read-write disk. Treat it like NOR flash — because it is one — and put anything
write-heavy in HyperRAM.

Caution: whatever the FPGA loading plan is, the flash still holds a bitstream at
address 0 today (the reference read starts `78 0a 00 20`, an ARM vector table).
Anything that writes to flash should not assume the space is unused until that
is deliberately reclaimed.

## Reading it over the sideband

`CMD_DEVICES` (`0x2C`) returns the three JEDEC ID bytes and a flags byte
(bit 0 HyperRAM present, bit 1 flash ID valid):

```
DEVICES raw: 41 ef 40 16 02 bd
  status 0x41  ok=1
  jedec  ef 40 16
  flags  0b00000010   hyperram=0 flash_valid=1
  crc    OK
```

98/100 transactions succeed with zero CRC corruption; the two misses are
USB-side timeouts from bit-banged-transmit preemption, already characterised in
the sideband speed work, not flash faults.

The status OK bit reflects `flash_valid` rather than being set unconditionally,
so a host cannot mistake power-on zeros for a device that genuinely identified
itself as `00 00 00`.

## Clock domains

`LunaECP5DomainGenerator` offers only **60, 120 and 240 MHz** — it drives the
sync domain from one of three PLL outputs and raises `KeyError` for anything
else. Intermediate SCK rates therefore need either a different clock generator
or a finer divider in `SPIStreamController`, whose current power-of-two counter
also cannot produce them.

`SPIStreamController` derives SCK from the top bit of a `range(period)` counter,
so `SCK = sync / period` with `period >= 2`; at `period = 1` the counter is
zero-width and elaboration fails outright. On a 60 MHz domain that caps SCK at
30 MHz — which happens to be exactly where the sampling path tops out anyway.
