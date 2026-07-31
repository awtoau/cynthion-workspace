# Configuration flash: identification, read modes and speed

The configuration flash on Cynthion r1.4, in detail: what the part is, how fast it goes,
what it supports, and how much of it there actually is.

## Capacity: exactly 4 MiB, verified three ways

Asked because the ECP5 on this board carries more usable silicon than its marking
suggests (`ecp5-test/fabric/FABRIC_TEST.md`, pluribus#98), and SPI NOR capacity is
literally one byte of the JEDEC ID -- so the question was cheap. See #109.

**The flash is what it says it is.** `scripts/flash_capacity_probe.py`, entirely
read-only:

| test | result |
|---|---|
| **SFDP density** | declares 4 MiB. The strong test -- the die publishes this independently of the ID byte |
| reads at 4, 8, 12 MiB | all alias offset 0 exactly |
| 4-byte addressing | absent, ADS clear, nothing responds past 16 MiB |

The aliasing comparison is sound because offset 0 held real bitstream data -- `Part:
LFE5U-12` is legible in the hex -- rather than erased `0xFF`. Two blank regions would
match trivially and prove nothing; that trap is why the probe compares against live data.

**Contrast with the HyperRAM on the same board, which is 8 MiB against a declared 4**
(`hyperram-detailed.md`). Same question, opposite answer, which is worth knowing before
assuming either way about a part.

The r1.4 configuration flash is a **Winbond W25Q32**, JEDEC ID `EF 40 16`
(manufacturer `EF`, type `40`, capacity `16` = 2^22 = **4 MiB**). Read by the
FPGA over SPI and confirmed independently by `apollo flash-info`, which reports
the same ID plus unique ID `355027cba3ac60de`.

If the FPGA is loaded over USB at startup rather than from flash, the whole
4 MiB is free — see [Using it as RISC-V storage](#using-it-as-risc-v-storage).

## Single-lane results

Every row was checked against the same region read through `apollo flash-read`,
an independent path (JTAG bit-banged by the SAMD11). A pass means two unrelated
mechanisms agree on the data, not that one is self-consistent.

| SCK | opcode | throughput | data | verdict |
|---|---|---|---|---|
| 15 MHz | `0x03` | 1.87 MB/s | matches | PASS |
| 30 MHz | `0x03` | 3.75 MB/s | matches | PASS |
| 30 MHz | `0x0B` | 3.75 MB/s | matches | PASS |
| 60 MHz | `0x03` | 7.49 MB/s | all zeros | FAIL |
| 60 MHz | `0x0B` | 7.49 MB/s | all zeros | FAIL |

Five reconfigurations at 30 MHz returned bit-identical cycle counts and CRCs.

Both opcodes fail identically at 60 MHz. `0x0B` is rated to 104 MHz and `0x03`
only to 50 MHz, so if the flash were the limit the fast-read variant would have
survived where the plain read died. It did not. The failure signature is all
zeros — a dead MISO, not corrupted or shifted data.

> **Superseded.** These 60 MHz failures were measured before the missing
> chip-deselect was found. With that fixed, 60 MHz passes -- see the runtime
> sweep below. Read this table as "what a build with the deselect bug did", not
> as a frequency ceiling.

**Trap for a future reader:** the original detector was an XOR fold, which
cancels on any even-length run of constant data — a dead MISO and a healthy read
of 4096 zeros both folded to `0x00`. Two conclusions were drawn from that broken
detector before a 1 KiB block-RAM mirror of each read disproved them. It is now
a CRC-8.

## What the part supports

From the datasheet (99 pages, revision J):

| Mode | Opcode | Max clock | Lanes | Ceiling | Available here |
|---|---|---|---|---|---|
| Read | `0x03` | 50 MHz | 1 | 6.25 MB/s | yes, working |
| Fast read | `0x0B` | 104 MHz | 1 | 13 MB/s | yes, working |
| Fast read dual output | `0x3B` | 104 MHz | 2 | 26 MB/s | pins wired, not implemented |
| Fast read quad output | `0x6B` | 104 MHz | 4 | 52 MB/s | implemented, see below |
| Fast read dual I/O | `0xBB` | 104 MHz | 2 | 26 MB/s | pins wired, not implemented |
| Fast read quad I/O | `0xEB` | 104 MHz | 4 | 52 MB/s | pins wired, not implemented |
| Word read quad I/O | `0xE7` | 104 MHz | 4 | 52 MB/s | pins wired, not implemented |

The platform declares a `qspi_flash` resource with all four data lines
(`T8 T7 M7 N7`), so quad mode needs gateware, not rework.

### Double-edge clocking (DDR)

**Not supported by this part.** The datasheet contains no DTR opcodes — `0Dh`,
`BDh` and `EDh` are absent and the string "DTR" appears nowhere in it. DDR reads
belong to the W25Q-DTR family, which this is not.

Trap: the datasheet advertises "equivalent clock rates of 208 MHz (104 MHz × 2)
for Dual I/O and 416 MHz (104 MHz × 4) for Quad I/O". That is lane parallelism,
not double-edge clocking — each lane is still sampled once per clock. HyperRAM
*is* a genuine DDR device, so double-edge work belongs there rather than here.

## Using it as RISC-V storage

Since the bitstream is loaded over USB at startup, the full 4 MiB is available.

| Access pattern | Rate today | With quad at 30 MHz | With quad at 50 MHz |
|---|---|---|---|
| Sequential read | 3.75 MB/s | 15 MB/s | 25 MB/s |
| 4 KiB block | ~1.1 ms | ~0.27 ms | ~0.16 ms |
| 256-byte page | ~68 µs | ~17 µs | ~10 µs |

Write and erase times, from the datasheet:

| Operation | Unit | Typical | Max |
|---|---|---|---|
| Page program | 256 bytes | 0.7 ms | 3 ms |
| Sector erase | 4 KiB | 45 ms | 400 ms |
| Block erase | 32 KiB | 120 ms | 1600 ms |
| Block erase | 64 KiB | 150 ms | 2000 ms |
| Chip erase | 4 MiB | 10 s | 50 s |

Reads are random-access: any address can start a sequential burst that runs to
the end of the chip, with no page boundary penalty. Writes are page-bound and
erase is required before rewriting. Endurance is more than 100,000 erase/program
cycles per sector with 20-year retention, so anything write-heavy needs wear
levelling. Continuous Read with 8/16/32/64-byte wrap is supported, and QPI mode
can address in as few as 8 clocks.

Read-mostly filesystem or executable store, not a general read-write disk. Put
anything write-heavy in HyperRAM.

Caution: the flash still holds a bitstream at address 0 today (the reference read
starts `78 0a 00 20`, an ARM vector table). Anything that writes to flash should
not assume the space is unused until that is deliberately reclaimed.

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
else.

`SPIStreamController` derives SCK from the top bit of a `range(period)` counter,
so `SCK = sync / period` with `period >= 2`; at `period = 1` the counter is
zero-width and elaboration fails. On a 60 MHz domain that caps SCK at 30 MHz.
Intermediate SCK rates need either a different clock generator or a finer
divider than this power-of-two counter.

## Quad SPI (Fast Read Quad Output, 0x6B)

Verified byte-exact against `apollo flash-read`:

| divisor | SCK | lanes | throughput | verdict |
|---|---|---|---|---|
| 1 | 30 MHz | 4 | 14.92 MB/s | PASS (offset 0) |
| 1 | 30 MHz | 1 | 3.75 MB/s | PASS (single-lane path) |

Exactly 4× the single-lane rate at the same SCK, so the gain is lane count
rather than clock rate.

The controller is Glasgow's (`glasgow.gateware.qspi`, 0BSD). There is no quad
SPI core in Amaranth, amaranth-soc or LUNA; Glasgow's targets Lattice and
references this same W25Q32 family.

The Quad Enable bit is **already set** on this board (SR2 = 0x02), so quad needs
no configuration write and no loss of hardware write protection — setting QE
would repurpose /WP and /HOLD as IO2 and IO3.

### Bugs found and fixed

**CS was inverted twice.** Glasgow's controller inverts chip select itself,
expecting an active-high port, while the platform declares `cs` with `PinsN`, so
the port already carries `invert=True`. The two cancelled: CS was never asserted,
and every read returned zeros at the correct speed for every offset and divisor.
Offset-independence is the tell — a sampling error shifts or corrupts data, it
does not silence it.

**The read finished one byte early.** `done` fired when the last byte was
*requested* rather than when it returned, and the controller's pipeline is
several cycles deep, so the tail was lost: 8 requested returned 7. Found in
simulation, not on hardware.

**The chip was never deselected after a read.** The reader relied on the `chip`
field of the last payload beat, but by then `bytes_left` is 0, so
`i_stream.valid` is low and that frame is never sent. CS stayed asserted and the
flash was left mid-stream. A *single* read still worked, so this hid behind every
rebuild-and-reconfigure measurement and only surfaced once reads could be
repeated without reconfiguring.

**The DDR SCK bit was discarded.** Glasgow drives SCK through a DDR output
register, so `sck.o` is two bits — bit 0 for the first half of the sync cycle,
bit 1 for the second — placing clock edges at half-cycle resolution:

```
sck.o[0] = timer*2 >  divisor      # first half
sck.o[1] = timer*2 >= divisor      # second half
```

This code forwards `ddr_o[0]` alone, because `USRMCLK` takes a single clock
input:

```python
m.d.comb += self.sck.eq(self._sck_port.ddr_o[0])
```

At divisor 1 and above the two halves are equal, so bit 0 carries the whole
waveform. At divisor 0 the period exists *only* as the difference between the
halves (`0,1` in a single sync cycle), so bit 0 alone is a constant `0`: no
clock, no transaction, an idle bus. Divisor 0 failed identically at every sample
offset, which matches.

The fix is to serialise both halves into one signal before `USRMCLK`, using an
`ODDRX1F` output register clocked at 2× — the same construction LUNA already uses
to drive the HyperRAM clock (`i_D0`/`i_D1` into `o_Q`).

> Both descriptions are of divisor 0 failing; they differ in what the failure
> looked like on different builds ("all zeros" against "every byte wrong"). The
> divisor-0 path discards the clock-enable bit, so nothing coherent reaches the
> flash either way. The exact garbage returned is not diagnostic.

### Runtime clock control

The divisor is a register in the bitstream, not a build-time constant, so SCK can
be changed over JTAG without rebuilding: a full sweep takes about 30 seconds
instead of roughly five minutes per point.

120 MHz sync, sample offset 0, first 64 bytes checked against `apollo flash-read`:

| divisor | SCK | throughput | verdict |
|---|---|---|---|
| 0 | 120 MHz | 59.7 MB/s | FAIL — every byte wrong |
| 1 | 60 MHz | 29.85 MB/s | PASS |
| 2 | 40 MHz | 19.90 MB/s | PASS |
| 3 | 30 MHz | 14.93 MB/s | FAIL — 22/64 bytes differ |
| 4 | 24 MHz | 11.94 MB/s | PASS |
| 5 | 20 MHz | 9.95 MB/s | PASS |
| 7 | 15 MHz | 7.46 MB/s | PASS |

Divisor 3 fails while both faster (1, 2) and slower (4, 5, 7) divisors pass,
repeatably across three runs. At 60 MHz sync a different divisor failed. Failures
that move with the build rather than with SCK point at place-and-route variation
on the sample path, not at the part. Addressing them means constraining that path
or sweeping the sample offset per divisor, not clocking slower.

## The ceiling is the ECP5 pin, not the flash

The flash is rated to 104 MHz. **The ECP5 pin driving it is specified only to
62 MHz.**

`MCLK` is a configuration pin and never stops being one. The sysCONFIG note
(FPGA-TN-02039) states that on entering user mode "the Master SPI configuration
port pins are tristated with a weak pull-up. This allows the SPI pins to be used
as user I/O **except MCLK/CCLK which is tristated**." So `CS` and `IO0-3` become
ordinary user I/O; the clock stays owned by the configuration block and the only
route to it is the `USRMCLK` macro. The platform definition gives it no ball
number — "SCK is on pin 9; but doesn't have a traditional I/O buffer" — because
it cannot be requested as a pin. The signal travels fabric → USRMCLK →
configuration-block mux → pad, through silicon the data lines never touch, and
that path is what Lattice characterises only to 62 MHz:

    MCLK Frequency (MHz): 2.4  4.8  9.7  19.4  38.8  62

The family datasheet (FPGA-DS-02012) gives `fMCLK` only as a ±20% tolerance on
"all selected frequencies" — no maximum is quoted above 62 MHz. For comparison it
specifies `fCCLK`, the configuration clock *input*, at 60 MHz max.

| SCK | vs the 62 MHz spec | result |
|---|---|---|
| 40 MHz | within spec | PASS |
| 53.3 MHz | within spec | PASS (26.53 MB/s) |
| **62 MHz** | **the documented ceiling** | — |
| 80 MHz | +29% over | PASS, three runs, byte-exact |
| 120 MHz | +93% over | FAIL |
| 160 MHz | +158% over | FAIL |

80 MHz is **measured, not specified**: byte-exact three times, on one board, at
room temperature, at nominal voltage. Lattice publishes nothing above 62 MHz for
this pin, so there is no margin figure to reason from, and the failure mode is
not graceful — 120 MHz corrupts every byte rather than degrading. Reasonable for
instrumentation on hardware you can re-verify (the ladder takes about 30
seconds); poor to bake into anything expected to work on an untested or warm
board.

**53.3 MHz at 26.53 MB/s is the fastest verified point Lattice actually
specifies**, and is what to use where margin matters.

> The pin rating explains failures *above* 62 MHz. It cannot explain divisor 3,
> which fails at 30 MHz while both 60 MHz and 24 MHz pass -- a fault that is not
> monotonic in frequency is not a timing ceiling. That one is place-and-route
> variation on a particular build. The two causes are separate and neither
> subsumes the other.

### The pinout is forced, not a board fault

| FPGA pin | Function | Flash |
|---|---|---|
| T8 | `D0/PICO/IO0/PB11B` | IO0 |
| T7 | `D1/POCI/IO1/PB11A` | IO1 |
| M7 | `D2/IO2/PB9B` | IO2 |
| N7 | `D3/IO3/PB9A` | IO3 |
| N8 | `CSSPI/PB15A` | CS |
| **N9** | **MCLK/CCLK** | **CLK** |

sysCONFIG: "The MCLK is **always reserved** for use in MSPI mode, in most
post-configuration applications, as the reference clock for performing memory
transactions with the external SPI PROM." If the FPGA is to configure itself from
this flash, the clock has to be on N9; there is no alternative ball. The data
pins carry dual designations (`PB11A`, `PB11B`, `PB9A`, `PB9B`) — ordinary bank-8
I/O as well as MSPI pins, which is why they keep working at full speed after
configuration. MCLK has no alternate function. The asymmetry is in the silicon,
not the layout.

A board that never boots from flash escapes this: nothing needs MSPI, the
reservation applies "in **most** post-configuration applications" (a convention,
not a hardware rule), and when `CFGMDN[2:0]` is not in MSPI mode even `CSSPIN`
reverts to general-purpose I/O. Such a board could put the flash clock on an
ordinary bank pin and run it at whatever the flash allows — 104 MHz for quad,
roughly 52 MB/s, against the an unmeasured rate measured at 80 MHz here. Two caveats: on
r1.4 the only copper to the flash clock is from N9, so this is a PCB change for a
future revision, not a rework of boards in hand; and it costs the recovery path,
since a board that cannot configure itself from flash depends entirely on the
debug controller to load a bitstream.

> **Withdrawn.** No throughput was measured at 80 MHz. The fastest figure in
> this document is 29.85 MB/s at 60 MHz.

## Consequences for going faster

Raising fabric fmax does not help; the bottleneck is off-chip. Yosys `-retime`
was tried (with `-noabc9`, since the two are incompatible) and moved the design
between 145 and 157 MHz — inside the run-to-run variation of the default flow.

The useful direction is **fewer clocks per byte**:

- **Quad I/O (`0xEB`)** rather than quad output (`0x6B`) sends the address on
  four lanes too, saving six clocks per transaction.
- **Continuous Read** with wrap avoids re-sending the opcode on sequential
  bursts.
- **QPI mode** addresses in as few as 8 clocks.

None of these need a faster pin.


## Open work

| issue | what | blocked on |
|---|---|---|
| **#89** | SPI/QSPI parked at 48 MHz quad -- the deliberately-unfinished list: broken burst sequencer, dual modes (`0x3B`/`0xBB`) unimplemented, QPI and Continuous Read untried, 80 MHz verified on one board only | nothing -- these are buildable now |
| **#93** | Small reads, writes and soak | **a RISC-V core.** A JTAG register read takes ~35 ms against a ~1 us flash read, so the instrument is 35,000x slower than the thing measured. No host-side arrangement fixes that |
| #100 | Reaching the achievable speed on the loading path | see `../apollo_samd11_mcu/apollo-configure-speed-investigation.md` |
| #109 | The capacity question above | **answered for the flash** -- clean negative, nothing further unless someone tries vendor-specific commands |

The recurring theme is #93's: **writes and erases are entirely untested, and everything
here is reads.** That is the largest single gap in this document.

---

# Merged from `flash-detailed.md`

That document covered the same part from the same angle -- the chip, its measured
throughput, the pin ceiling, clocking -- and is retired to `debris/docs/`. What follows is
the material it held that this one did not.

## Vendor maximums (datasheet, not measured)

From the Winbond W25Q32JV datasheet, for reference against the measured table
below. These are what the *part* can do; what this *board* reaches is lower, and
for a reason given in [the FPGA pin section](#the-ceiling-is-the-fpga-pin-not-the-flash).

| Operation | Opcode | Max clock | Lanes | Vendor ceiling |
|---|---|---|---|---|
| Read | `0x03` | 50 MHz | 1 | 6.25 MB/s |
| Fast read | `0x0B` | 104 MHz | 1 | 13 MB/s |
| Fast read dual | `0x3B` / `0xBB` | 104 MHz | 2 | 26 MB/s |
| Fast read quad | `0x6B` / `0xEB` | 104 MHz | 4 | **52 MB/s** |

Write and erase, from the same datasheet (tPP, tSE, tBE, tCE):

| Operation | Size | Typical | Max |
|---|---|---|---|
| Page program | 256 B | 0.7 ms | 3 ms |
| Sector erase | 4 KiB | 45 ms | 400 ms |
| Block erase | 32 KiB | 120 ms | 1,600 ms |
| Block erase | 64 KiB | 150 ms | 2,000 ms |
| Chip erase | 4 MiB | 10 s | 50 s |

**Write tops out around 0.37 MB/s** (256 B / 0.7 ms typical), falling to
0.085 MB/s at the worst-case 3 ms. A full 4 MiB erase-and-rewrite is ~22 s
typical. That is **~70× slower than reads on this board**, and the two ceilings
have different causes: reads are limited by the FPGA's `USRMCLK` pin, writes by
the flash die itself.

**There is no bus-side trick for writes, and the vendor says so.** On Quad Input
Page Program (`0x32`), the datasheet states it helps "applications that have slow
clock speeds <5MHz" and that "systems with faster clock speed will not realize
much benefit … since the inherent page program time is much greater than the time
it takes to clock-in the data." At 48 MHz, shifting 256 bytes takes ~43 µs against
700 µs of internal programming — the bus idles ~94% of the time and quad recovers
about 4% end to end. The only things that move the number are workflow, not
silicon: poll the BUSY bit (SR1 bit 0, via `0x05`) instead of delaying for
worst-case, which recovers most of the 4× typ/max spread; and erase at the
largest granularity actually being replaced, since one 64 KiB block erase
(150 ms) beats sixteen 4 KiB sector erases (720 ms).

Two related notes from the datasheet:

- **Output drive strength defaults to 25%.** `DRV1/DRV0` in SR3 default to `1,1`;
  100% is available. This affects reads only. It is writable *volatile* via Write
  Enable for Volatile Status Register (`0x50`) then `0x11`, so it can be tested
  without any non-volatile write. Worth trying against the non-monotonic clock
  results in [flash-detailed.md](flash-detailed.md) — 30 MHz fails while 24 and 40 MHz
  pass, which a weak driver into pin capacitance could explain.
- **Erase/Program Suspend (`0x75`/`0x7A`)** interrupts a page program or sector
  erase to service a read, resuming afterwards. A latency tool, not a throughput
  one. The datasheet warns that power loss while suspended may corrupt the page or
  sector being written.

Datasheet consulted for this section was the DigiKey mirror of the JV revision
(self-labelled "Preliminary-Revision A1"); its timing tables agree with the FV
revision J used elsewhere in these notes. Mouser's link for the same part serves
a 14 KB stub rather than the PDF.

## Two bugs worth remembering

**CS was inverted twice.** Glasgow's controller inverts chip select internally,
expecting an active-high port, while the platform declares `cs` with `PinsN`.
The two cancelled: CS was never asserted, and every read returned zeros at the
correct speed for every offset and divisor. Offset-independence is what
identified it — a sampling error shifts or corrupts data, it does not silence it.

**The chip was never deselected.** The reader relied on the `chip` field of the
final payload beat, but by then `bytes_left` is 0 so `valid` is low and that
frame was never sent. A *single* read still worked, so this hid behind every
rebuild-and-reconfigure measurement and only appeared once reads could repeat
without reconfiguring. Fixing it doubled the apparent ceiling.

## What is NOT done

- **The burst sequencer is broken.** It asserts `start` on the completion edge
  while the reader has not returned to IDLE, so `busy` latches high. Committed
  deliberately, marked in the source.
- **Small-read performance is unmeasured.** A JTAG register read takes ~35 ms;
  a 4-byte flash read takes ~1 µs. The instrument is 35,000× slower than the
  thing measured, so the host cannot time short transfers however the gateware
  is arranged. This needs a soft CPU inside the FPGA.
- **`0xEB`'s benefit is predicted, not measured.** The 42%/19% figures above are
  arithmetic. Confirming them needs the same in-FPGA measurement.
- **Dual modes (`0x3B`, `0xBB`) are unimplemented**, as are Continuous Read with
  wrap and QPI mode — all of which reduce clocks per byte rather than needing a
  faster pin.
- **80 MHz is verified on one board only**, at room temperature and nominal
  voltage, and the failure mode is abrupt rather than graceful.
- **Write and erase are untested.** Everything here is reads.

## Files

| Path | What |
|---|---|
| `repos/apollo/apollo_fpga/gateware/qspi_flash.py` | Glasgow controller wrapper, USRMCLK bridge, quad reader |
| `repos/apollo/apollo_fpga/gateware/flash_id.py` | JEDEC ID, status registers, capture buffer |
| `repos/apollo/apollo_fpga/gateware/variable_clock.py` | ecppll-driven PLL |
| `ecp5-test/qspi/qspi_gateware.py` | Test bitstream |
| `scripts/qspi_ladder.py` | Divisor/offset sweep, verified against apollo |
| `scripts/qspi_burst.py` | Small-read comparison (blocked, see above) |
| `scripts/flash_modes.py` | Opcode and clock sweep |

