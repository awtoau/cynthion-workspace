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

### The real ceiling is the ECP5 pin, not the flash

The flash is rated to 104 MHz. It never gets the chance: **the ECP5 pin driving
it is specified only to 62 MHz.**

`MCLK` is a *configuration* pin, and unlike the others it never stops being one.
The sysCONFIG note is explicit about the asymmetry: on entering user mode "the
Master SPI configuration port pins are tristated with a weak pull-up. This
allows the SPI pins to be used as user I/O **except MCLK/CCLK which is
tristated**."

So `CS` and `IO0-3` become ordinary user I/O with ordinary buffers, while the
clock does not. It stays owned by the configuration block and the only way to
drive it is the `USRMCLK` macro. The platform definition says the same thing in
one line -- "SCK is on pin 9; but doesn't have a traditional I/O buffer" -- and
gives it no ball number, because it cannot be requested as a pin.

The signal therefore travels fabric → USRMCLK → configuration-block mux → pad,
through silicon the data lines never touch, and that path is what Lattice
characterises only to 62 MHz.

### This is not a board design fault

Worth stating plainly, because the constraint is easy to mistake for one. The
r1.4 schematic is correct and had no better option:

| FPGA pin | Function | Flash |
|---|---|---|
| T8 | `D0/PICO/IO0/PB11B` | IO0 |
| T7 | `D1/POCI/IO1/PB11A` | IO1 |
| M7 | `D2/IO2/PB9B` | IO2 |
| N7 | `D3/IO3/PB9A` | IO3 |
| N8 | `CSSPI/PB15A` | CS |
| **N9** | **MCLK/CCLK** | **CLK** |

The sysCONFIG note is unambiguous: "The MCLK is **always reserved** for use in
MSPI mode, in most post-configuration applications, as the reference clock for
performing memory transactions with the external SPI PROM." If the FPGA is to
configure itself from this flash — the entire purpose of a configuration flash
— the clock has to be on N9. There is no alternative ball; the boot ROM drives
that one and nothing else.

Note also that the data pins carry dual designations (`PB11A`, `PB11B`, `PB9A`,
`PB9B`): they are ordinary bank-8 I/O as well as MSPI pins, which is precisely
why they keep working at full speed once configuration is done. MCLK has no
such alternate function. The asymmetry is in the silicon, not in the layout.

Routing the flash clock to a general-purpose pin instead would mean the FPGA
could no longer boot from it — trading the board's whole configuration
mechanism for read throughput on a peripheral.

### Unless the board never boots from flash

That trade changes completely if the bitstream is loaded over USB at startup,
which is the plan here. Then nothing needs MSPI, and the sysCONFIG note's
reservation stops applying: it says MCLK is reserved "in **most**
post-configuration applications", a convention rather than a hardware rule, and
that when `CFGMDN[2:0]` is not in MSPI mode even `CSSPIN` reverts to
general-purpose I/O.

So a board that loads its bitstream over USB *could* put the flash clock on an
ordinary bank pin and run it at whatever the flash allows — 104 MHz for quad,
which is roughly 52 MB/s, against the ~40 MB/s measured at 80 MHz here.

Two caveats:

**It is a PCB change, not a rework.** On r1.4 the only copper to the flash
clock is from N9. No gateware or configuration change can move it, so this
applies to a future revision rather than to boards in hand.

**It costs the recovery path.** A board that cannot configure itself from flash
depends entirely on the debug controller to load a bitstream. That is fine
while Apollo is healthy and awkward when it is not — the flash is what makes
the FPGA independently bootable.

Worth designing in only if the USB loading path is considered reliable enough
to be the sole one.

After configuration the pin can be borrowed for user logic through `USRMCLK` — the sysCONFIG technical note
(FPGA-TN-02039) says the device "provides a solution for users to choose any
user clock as MCLK" — but the frequencies Lattice specifies for it are the
configuration ones, and that table stops at **62 MHz**:

    MCLK Frequency (MHz): 2.4  4.8  9.7  19.4  38.8  62

The family datasheet (FPGA-DS-02012) gives `fMCLK` only as a ±20% tolerance on
"all selected frequencies" — no maximum is quoted above 62 MHz, because the pin
was never intended to run there. For comparison it specifies `fCCLK`, the
configuration clock *input*, at 60 MHz max.

The measurements line up with that exactly:

| SCK | vs the 62 MHz spec | result |
|---|---|---|
| 40 MHz | within spec | PASS |
| 53.3 MHz | within spec | PASS |
| **62 MHz** | **the documented ceiling** | — |
| 80 MHz | +29% over | PASS, three runs, byte-exact |
| 120 MHz | +93% over | FAIL |
| 160 MHz | +158% over | FAIL |

So 80 MHz works but is **measured, not specified**. It was verified byte-exact
three times -- on one board, at room temperature, at nominal voltage. Lattice
publishes nothing above 62 MHz for this pin, so there is no margin figure to
reason from, and the failure mode is not graceful: 120 MHz corrupts every byte
rather than degrading.

That makes it a reasonable choice for instrumentation on hardware you can
re-verify -- the ladder takes about 30 seconds -- and a poor one to bake into
anything expected to work on an untested board or a warm one.

**53.3 MHz at 26.53 MB/s is the fastest verified point Lattice actually
specifies**, and is what to use where the margin matters.

This retires several earlier diagnoses in this document. Failures attributed to
flash timing or to the sampling path were the pin running far beyond its rating.
Nothing about the W25Q32 was ever the constraint.

### Consequences for going faster

Raising fabric fmax does not help, because the bottleneck is off-chip. Yosys
`-retime` was tried (with `-noabc9`, since the two are incompatible) and moved
the design between 145 and 157 MHz — inside the run-to-run variation of the
default flow, and aimed at the wrong constraint regardless.

The useful direction is **fewer clocks per byte**, not faster ones:

- **Quad I/O (`0xEB`)** rather than quad output (`0x6B`) sends the address on
  four lanes too, saving six clocks per transaction.
- **Continuous Read** with wrap avoids re-sending the opcode on sequential
  bursts.
- **QPI mode** addresses in as few as 8 clocks.

None of these need a faster pin, which is what makes them the sensible path.

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
