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

**30 MHz is the verified ceiling for the single-lane path.** Five
reconfigurations there returned bit-identical cycle counts and CRCs. Quad
reaches 60 MHz SCK — see the runtime sweep below.

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

## Quad SPI (Fast Read Quad Output, 0x6B)

Verified working at **14.92 MB/s**, byte-exact against `apollo flash-read`:

| divisor | SCK | lanes | throughput | verdict |
|---|---|---|---|---|
| 1 | 30 MHz | 4 | **14.92 MB/s** | PASS (offset 0) |
| 1 | 30 MHz | 1 | 3.75 MB/s | PASS (single-lane path) |

Exactly **4× the single-lane rate at the same SCK**, so the gain is lane count
rather than clocking faster — which is the safer way to get it, since the
single-lane path had already run out of timing margin at 60 MHz.

The controller is Glasgow's (`glasgow.gateware.qspi`, 0BSD). There is no quad
SPI core in Amaranth, amaranth-soc or LUNA, and Glasgow's already targets
Lattice and references this same W25Q32 family.

The Quad Enable bit is **already set** on this board (SR2 = 0x02), so quad
needs no configuration write and no loss of hardware write protection — setting
QE would repurpose /WP and /HOLD as IO2 and IO3.

### Two bugs worth recording

**CS was inverted twice.** Glasgow's controller inverts chip select itself,
expecting an active-high port, while the platform declares `cs` with `PinsN`,
so the port already carries `invert=True`. The two cancelled: CS was never
asserted, and every read returned zeros at the correct speed for every offset
and divisor. The offset-independence is what identified it — a sampling error
shifts or corrupts data, it does not silence it.

**The read finished one byte early.** `done` fired when the last byte was
*requested* rather than when it returned, and the controller's pipeline is
several cycles deep, so the tail was lost: 8 requested returned 7. Found in
simulation, not on hardware.

### Runtime clock control, and a correction

The divisor is a register in the bitstream, not a build-time constant, so SCK
can be changed over JTAG without rebuilding. A full sweep now takes about 30
seconds instead of roughly five minutes per point.

That change immediately overturned an earlier conclusion. **60 MHz SCK works**:

| divisor | SCK | throughput | verdict |
|---|---|---|---|
| 0 | 120 MHz | 59.7 MB/s | FAIL — every byte wrong |
| 1 | **60 MHz** | **29.85 MB/s** | **PASS** |
| 2 | 40 MHz | 19.90 MB/s | PASS |
| 3 | 30 MHz | 14.93 MB/s | FAIL — 22/64 bytes differ |
| 4 | 24 MHz | 11.94 MB/s | PASS |
| 5 | 20 MHz | 9.95 MB/s | PASS |
| 7 | 15 MHz | 7.46 MB/s | PASS |

(120 MHz sync, sample offset 0, first 64 bytes checked against
`apollo flash-read`.)

Two things were wrong in the earlier write-up:

**"60 MHz fails" was a bug, not a limit.** The reader never deselected the chip
after a read — it relied on the `chip` field of the last payload beat, but by
then `bytes_left` is 0, so `i_stream.valid` is low and that frame is never
sent. CS stayed asserted and the flash was left mid-stream. A *single* read
still worked, so this hid behind every rebuild-and-reconfigure measurement and
only surfaced once reads could be repeated without reconfiguring. With it fixed,
60 MHz SCK verifies byte-exact at **double** the previously reported ceiling.

**The remaining failures are not a speed limit either.** Divisor 3 fails while
both faster (1, 2) and slower (4, 5, 7) divisors pass, repeatably across three
runs. At 60 MHz sync a different divisor failed. Failures that move with the
build rather than with SCK point at place-and-route variation on the sample
path, not at the part. Chasing them means constraining that path or sweeping
the offset per divisor, not clocking slower.

### Why 60–104 MHz is untested

The datasheet rates quad output to 104 MHz. 60 MHz passes and 120 MHz fails,
so the true ceiling is somewhere between — and the most interesting part of
that range, 60 to 104, is currently **unreachable**, which is a limitation of
the setup rather than a decision that it does not matter.

`SCK = sync / (divisor + 1)` with an integer divisor, so the reachable rates
are set by the sync frequency:

| sync | divisor 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| 120 MHz | 120 | **60** | 40 | 30 |
| 240 MHz | 240 | 120 | **80** | 60 |

240 MHz sync would put 80 MHz squarely in the window. It does not build: the
design closes timing at about **138 MHz**, and nextpnr refuses outright rather
than warning.

The first attempt at raising that was worth doing anyway — the critical path
was `block RAM → LUTs → JTAG instruction register`, entirely the test harness's
capture readback rather than anything to do with reading flash. Registering it
removed that path, but only moved fmax from 137 to 138 MHz: the next limit is
inside Glasgow's controller, on the skid buffer feeding the I/O streamer. That
is real logic doing real work, so lifting it means pipelining an upstream core.

Two ways to reach the window, then:

1. **A sync rate between 120 and 240 MHz.** 160 MHz with divisor 1 gives
   80 MHz SCK and is under the 138 MHz ceiling — but LUNA's generator only
   offers 60/120/240, and the custom PLL attempted here did not work (see
   below).
2. **Pipeline Glasgow's controller** so the design closes at 240 MHz.

The first is much cheaper, and makes the earlier PLL failure worth revisiting
rather than a dead end.

### The 60 MHz limit is ours, not the flash's

At `divisor = 0` the reads fail with all zeros. The part is rated to 104 MHz and
is not the limit here: it never receives a clock at all. The cause is one
discarded bit in this code.

Glasgow drives SCK through a **DDR output register**, so `sck.o` is two bits
rather than one — bit 0 is driven during the first half of the sync cycle, bit 1
during the second. That is what lets it place clock edges at half-cycle
resolution:

```
sck.o[0] = timer*2 >  divisor      # first half
sck.o[1] = timer*2 >= divisor      # second half

divisor=1:  timer=0 -> 0,0     timer=1 -> 1,1
            both halves agree; SCK is one full sync cycle low, one high (30 MHz)

divisor=0:  timer=0 -> 0,1
            a whole clock period inside one sync cycle (60 MHz), and it exists
            *only* as the difference between the two halves
```

This code forwards `ddr_o[0]` alone, because `USRMCLK` — the ECP5's
configuration-clock primitive, the only route to that pin — takes a single clock
input:

```python
m.d.comb += self.sck.eq(self._sck_port.ddr_o[0])
```

At divisor 1 and above the halves are equal, so bit 0 carries the whole
waveform and nothing is lost. At divisor 0 the halves differ, and keeping bit 0
alone leaves a constant `0`: no clock, no transaction, and the FPGA samples an
idle bus. That matches the symptom exactly — divisor 0 failed identically at
every sample offset, which is what "nothing happened" looks like rather than
"data arrived wrongly".

The fix is to serialise both halves into one signal before `USRMCLK`, using an
`ODDRX1F` output register clocked at 2× — the same construction LUNA already
uses to drive the HyperRAM clock (`i_D0`/`i_D1` into `o_Q`).

Raising this further therefore means either driving `USRMCLK` from a
double-rate output register, or raising the sync domain and keeping divisor 1.
Either would reach the 25 MB/s that 50 MHz SCK allows, and the part would still
have headroom above that.
