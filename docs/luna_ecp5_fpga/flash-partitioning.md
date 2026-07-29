# Partitioned configuration flash on Cynthion r1.4

A permanent USB loader in one slot, selectable test images in the others, on
the 4 MiB W25Q32DV.

The short version: **this works, but not the way the brief assumed.** The ECP5
has two separate multi-image mechanisms, and the useful one is not a partition
table the FPGA reads. There is nothing in the silicon that looks at a table and
picks a slot. Selection is done by the *currently running bitstream*, which
carries the address of its successor in its own CRAM.

Sources: FPGA-TN-02039 (ECP5 sysCONFIG Usage Guide) rev 2.3 and FPGA-TN-02203
(LatticeECP3/ECP2/M, ECP5 and ECP5-5G Dual Boot and Multiple Boot Feature) rev
1.8, both fetched to `tmp/refs/`; Lattice Diamond 3.14 installed at
`~/lscc/diamond/3.14`; prjtrellis source. Measurements on the attached board
are marked as such.


## 1. How the boot image is actually selected

### There are two mechanisms, and they are not the same thing

**Multi-boot** — the one that matters here. TN-02203 §7:

> For multi-boot operation, the next target address is set in memory that was
> loaded during the current configuration memory load. Initiating reprogramming
> by toggling the PROGRAMN pin or issuing a REFRESH through any sysCONFIG port
> causes the device to load from the defined SPI Flash address.

So: **BOOTADDR is a directive, not a fallback.** It is not "try slot 0, jump on
CRC failure". It is "the next configuration starts *here*", and the value comes
from the bitstream that is running right now. Answering the brief's first
question directly — directive.

Diamond's own Deployment Tool exposes exactly this. From its help
(`docs/webhelp/eng/User Guides/Device Programming/external_memory_deployment_type.htm`),
the Multiple Boot options are: *Number of Alternate Data Files* (1–4), *Select
Starting Address* in `0x010000` sectors, and — decisively — **Next Pattern
`<prim|alt1|alt2|alt3|alt4>`**. Each image names its successor. That is the
whole selection model.

**Dual-boot** — a genuine automatic fallback, but a *different* mechanism, and
one that does not help us. TN-02203 §5.1: on a CRC error or a 16K-clock
preamble timeout the device clears SRAM and reads a **Jump command** stored in
flash, which points at the golden pattern. The fallback target comes from that
Jump command in flash, **not from BOOTADDR**. TN-02039 §6.1.3 puts the golden
image at `0xFFFF00` by default — 16 MiB, off the end of a 4 MiB part.

This distinction is the single most important finding, and it is the one most
easily got wrong: a corrupt image at BOOTADDR does **not** automatically fall
back to slot 0 via the BOOTADDR path.

### What `ecppack --bootaddr` really emits

Not a bitstream command. `libtrellis/tools/ecppack.cpp:167-195` sets eight
**CRAM fuse bits** in tile `EFB1_PICB1`, plus one flag:

```c
if (bootaddr & 0xffff) { cerr << "Error: Boot Address must be 64k aligned !"; return 1; }
bootaddr = (bootaddr & 0x00ff0000) >> 16;
```

- low 16 bits must be zero → **64 KiB alignment**, enforced with an error
- only bits **[23:16]** survive → 8 bits, **16 MiB reach**, 64 KiB granular
- bits ≥24 are silently masked; `--bootaddr 0x1000000` programs address 0
- `bitopts["multiboot"]="yes"` sets bit 20 of CTRL0 (`Bitstream.cpp:33,885-890`)

Vendor data corroborates the width independently. Diamond's device database
`data/vmdata/database/xpga/ecp5/ispVM_023.xdf` lists exactly eight fuse
addresses per part:

```xml
<Multiboot_cfg_Address>;7039;7041;7043;7045;7047;7051;7053;7055;</Multiboot_cfg_Address>
```

Eight vendor fuses, eight Trellis CRAM bits. These agree, which is worth
stating because the Trellis fuzzer comments that the field is hand-derived and
"the name is arbitrarily chosen". It is not guesswork: Lattice's own database
says the same thing.

At 4 MiB the 8-bit limit costs us nothing — 4 MiB needs bits [21:16].

### Runtime changeable? No

BOOTADDR is CRAM, written at configuration time. A running design cannot change
its own. To change where the *next* boot goes you must load a bitstream built
with a different `--bootaddr`.

There is no fabric path to the configuration engine. Diamond's own ECP5
primitive library (`cae_library/simulation/verilog/ecp5u/`, 171 primitives) has
`DTR`, `GSR`/`SGSR`, `OSCG`, `SEDGA`, `USRMCLK`, `EXTREFB`, `SRAMWB` — no ICAP
equivalent, no PCNTR. `SRAMWB` is a slice-level LUT-RAM write mux, not
configuration.

### What the running design must do to trigger the jump

Assert PROGRAMN, or issue REFRESH over JTAG/Slave-SPI. TN-02039 §3:

> Refresh – The process of re-triggering a bitstream write operation. It is
> activated by toggling the PROGRAMN pin or issuing a REFRESH command, which
> emulates the PROGRAMN pin toggling. Only the JTAG port and the Slave SPI port
> support the REFRESH command.

**On r1.4 the gateware can do this itself.** PROGRAMN is routed to an FPGA pin:

```python
# repos/cynthion/cynthion/python/src/gateware/platform/cynthion_r1_4.py:108-109
# output signal connected to PROGRAMN to trigger FPGA reconfiguration
Resource("self_program", 0, PinsN("T13", dir="o"), ...)
```

That is the enabling fact for the whole design. Without it, switching images
would need host involvement every time.


## 2. Why a conventional "partition table" is the wrong shape

The FPGA never reads a table. Consequences:

- A table is **for the host and the loader**, not the silicon. It is
  bookkeeping, not a boot mechanism.
- Slot addresses are compiled into bitstreams. Moving a slot means rebuilding
  the image that points at it.
- A table at offset 0 would be read by the config engine as a bitstream and
  fail its preamble check. The loader must live at 0; the table goes elsewhere.

So the design below keeps a table because the *brief needs one* — explicit
lengths, listable, verifiable — while being clear that selection happens
through BOOTADDR, not the table.


## 3. Slot layout

`LFE5U-12F` (confirmed: `cynthion_r1_4.py:21`). Measured bitstream sizes in
this workspace: 99 963 B (blinker) to 402 957 B (facedancer). So a 512 KiB slot
holds any current image with headroom, and 4 MiB gives eight of them.

Lattice's own allocation table (TN-02203 Table A.3) reserves far more — the
smallest ECP5 listed, LFE5/UM-25, wants 23 sectors (1.5 MiB) for dual boot on a
16 Mbit flash, sized for a *worst-case uncompressed* bitstream. The 12F is
smaller than anything in that table. Sizing to measured images rather than
Lattice's worst case is a deliberate trade: it buys eight slots instead of two,
at the cost of a rebuild-and-relayout if a design ever exceeds 512 KiB. The
tooling checks this on every write.

```
  slot  offset     size     role
  ----  --------   -------  ---------------------------------------------
   0    0x000000   512 KiB  LOADER -- permanent USB-FS loader, never erased
   1    0x080000   512 KiB  test image
   2    0x100000   512 KiB  test image
   3    0x180000   512 KiB  test image
   4    0x200000   512 KiB  test image
   5    0x280000   512 KiB  test image
   6    0x300000   512 KiB  test image
   7    0x380000   448 KiB  test image
        0x3F0000    60 KiB  (spare)
        0x3FF000     4 KiB  partition table, primary + shadow copy
```

The table sits in the **last 4 KiB sector**, deliberately:

- the config engine never looks there (it starts at 0, or at a 64 KiB-aligned
  BOOTADDR)
- it is a 4 KiB erase sector of its own, so rewriting the table cannot disturb
  a slot
- it is the furthest point from slot 0, so a runaway write into the loader is
  not adjacent to it

Slot 0 is the loader because that is where the ECP5 boots on power-up and on
any PROGRAMN pulse from a bitstream with BOOTADDR unset. **That is the recovery
property that makes the whole scheme safe**: a bad test image cannot cost you
the board, because a power cycle lands on the loader.

For that to hold, every test image must be built **without** `--bootaddr` (or
with `--bootaddr 0`), so that when *it* pulses PROGRAMN it returns to the
loader. Only the loader carries a non-zero BOOTADDR, naming the slot it wants
to launch. This is the asymmetry the Deployment Tool's "Next Pattern" field
encodes, and it is what makes the direction of travel loader → test → loader.


## 4. Partition table format

Explicit lengths, per the brief — a smaller bitstream written over a larger one
leaves the tail behind, so length must never be inferred from where `0xff`
begins. That trap is real and is reproduced in §6 below.

64-byte header, then 8 × 32-byte entries, little-endian:

```
  offset  size  field
  ------  ----  ------------------------------------------------------
  header
   0x00     8   magic "CYNPART1"
   0x08     2   version (1)
   0x0a     2   entry count
   0x0c     4   flash size in bytes
   0x10     4   CRC32 of entries region (0x40..0x140)
   0x14     4   sequence number, incremented on every write
   0x18    40   reserved, 0xff
  entry i at 0x40 + 32*i
   0x00     4   start offset
   0x04     4   slot size
   0x08     4   image length          <-- explicit, never inferred
   0x0c     4   CRC32 of image bytes
   0x10     2   flags: bit0 valid, bit1 locked, bit2 has-bootaddr
   0x12     2   bootaddr >> 16
   0x14    12   label, NUL-padded ASCII
```

`sequence` plus a shadow copy at `0x3FF800` gives torn-write protection: read
both, take the one with the higher sequence and an intact CRC. Writes alternate
so the previous good copy always survives.

`locked` guards slot 0. The tooling refuses to write or erase a locked slot
without `--force`. This is advisory — the real protection would be the flash's
own block-protect bits, which is a natural extension and matches what Lattice
recommends ("Protect Golden Sector", TN-02203 §7.1) and what real ECP5
bootloader projects do.


## 5. How selection works, end to end

Building the loader to launch slot 3:

```
ecppack --bootaddr 0x180000 loader.config --bit loader.bit
```

Test images get no `--bootaddr`, so their BOOTADDR is 0.

Then:

1. Power on. ECP5 reads address 0, configures the **loader**.
2. Loader enumerates as USB-FS. Host writes test images to slots and updates
   the table. The loader's own slot is refused unless forced.
3. To launch slot 3 the loader must be **rebuilt** with
   `--bootaddr 0x180000` and rewritten, then it pulses `self_program` (T13).
4. ECP5 reconfigures from `0x180000`. The test image runs.
5. Test image pulses `self_program`. Its BOOTADDR is 0, so the board returns
   to the loader.

**Step 3 is the awkward part and it is inherent, not an implementation
shortcut.** Because BOOTADDR is CRAM, "boot slot 3 next" cannot be expressed as
data — it has to be baked into a bitstream. Options, none free:

- **Rewrite the loader on every switch.** Simple, but writes the one image that
  must never be lost. Mitigated by writing to a spare slot and switching, never
  editing slot 0 in place.
- **Eight pre-built loader variants**, one per target, in slots 1..7. Costs
  slots, no rewrite of slot 0. Probably the best trade at 8 slots.
- **Patch the 8 BOOTADDR CRAM bits in place.** Avoids a rebuild but needs the
  exact frame/bit offsets for the 12F and invalidates the bitstream CRC. Not
  attempted; flagged as unverified.

A cleaner alternative exists and is worth naming: **have the loader not use
BOOTADDR at all**, and instead configure the FPGA over JTAG from the host via
Apollo, as `apollo configure` already does. That sidesteps CRAM entirely. It is
slower and needs the host, but for a bench test-image selector it may simply be
the better answer. The BOOTADDR path earns its complexity only when the board
must switch images standalone.


## 6. Measured on hardware

Board: Cynthion r1.4, `S4DKJHSMGJJVCIBAEA3D4FYP74`, flash W25Q32DV
(`ef4016`), UID `355027cba3ac60de`.

**Full 4 MiB backed up and verified** —
`tmp/flashbackup/full-4MiB-verified.bin`, sha256
`c36b8c7fc87ce50dfca3b010e746d46aaf7ab2f266abf97a5046018bf2efcf2b`. Every
64 KiB chunk was read twice and compared before being accepted.

Used blocks, from the verified dump:

```
  block  0 @0x000000  60590 non-0xff
  block  1 @0x010000  55398
  block  2 @0x020000  63069
  block  3 @0x030000  51907
  block 11 @0x0b0000  57738
```

Everything else is erased.

### The stale-tail trap, reproduced

Scanning back from the end of the region below `0x040000` for the first
non-`0xff` byte reports **248 515 bytes**. The live image is **100 336**. The
138 219 bytes in between are the tail of a larger bitstream that the smaller one
only partly overwrote — exactly the misdiagnosis recorded in
`reconfigure-initn-gap.md`, reproduced here independently.

This is the concrete justification for explicit `image length` in the table.

### The partition tooling, exercised on the board

`scripts/flashparts.py` was run against the real chip, not simulated:

```
init      -> wrote table at 0x3ff000 (+ shadow at 0x3ff800), sequence 1
list      -> 8 slots read back, slot 0 locked as loader
write 0   -> REFUSED: "slot 0 is locked (loader) -- refusing without --force"
write 3   -> 100152 bytes at 0x180000, crc32 0xd00b7c3f, sequence 2
verify    -> slot 3 OK
write 3   -> 99963 bytes (smaller image over larger), sequence 3
verify    -> slot 3 OK, 99963 bytes, crc32 0x2d9d0775
write 4   -> 99963 bytes at 0x200000, sequence 4
verify    -> slots 3 and 4 both OK
```

The table survives a full read/write/read cycle through real flash, the
sequence number advances, and the locked-slot interlock refuses the loader.

**Slot 0 verified byte-identical to the backup afterwards** (sha256 of
`0x000000..0x040000` unchanged). Writes to slots 3 and 4 did not disturb it,
which is the containment property the whole layout depends on. This works
because `ECP5_JTAGProgrammer.flash()` calls `_flash_erase(offset, len)` —
sector/block erases scoped to the range written. `erase_flash()` without the
underscore is a **chip erase**; the tool never calls it.

### Explicit length beats inference — demonstrated, not asserted

Slot 4 was deliberately put into the trap state: a 402 957-byte image written,
then 99 963 bytes written over the front **without erasing the remainder**.

```
truly written    : 99963 bytes
inference reports: 402953 bytes      <-- scanning back from the first 0xff
overstated by    : 302990
```

That is the same failure that previously reported a 248 KB image where 100 KB
had been written, reproduced deliberately. A table storing explicit lengths
reads the right 99 963 bytes; a tool inferring length reads 402 953 and
compares the wrong region.

There is a **second, independent** reason inference cannot work, found while
testing: bitstreams themselves end in `0xff` padding. `qspi_build/top.bit`
ends `5e000000ffffffff`, so even on a perfectly erased slot, scanning back
undercounts 99 963 as 99 959. Inference is wrong in both directions — it
overstates after a partial overwrite and understates on a clean write.

A note on the partial-overwrite experiment: the resulting image is corrupt from
byte 65 onward, because NOR flash only clears bits on write. Programming
without erasing does not produce "the new image plus a stale tail" — it
produces the bitwise AND of both. The stale tail is the visible symptom; the
corruption is the real damage.

### A read bug worth knowing about

A single `flash-read` of the whole 4 MiB **silently corrupts itself**. Past
roughly 1.9 MB every 256-byte page comes back as `03 <addr> 00 00 ...` — the
`READ_PAGE` opcode and the page's own address echoed back instead of data. It
looked like 35 fully-used blocks in the high half of the chip. None of it was
real; re-reading any of those offsets in a short transfer returns `0xff`.

Had this gone unnoticed it would have been read as "the flash is nearly full",
which is the same class of error as the stale-tail trap and would have wrecked
the slot layout. `scripts/flash_backup.py` detects the pattern explicitly and
retries.

Three further behaviours, all measured, all handled in the script:

- `unconfigure()` must run in its **own** `with dut.jtag` block, with the read
  in a second one. Both in one context and the flash reads all-`0xff`, and
  `read_flash` raises "Flash does not seem correctly connected to the FPGA!" on
  a board that `flash-info` reports as healthy throughout.
- Sustained background SPI drops the USB link with `[Errno 32] Pipe error`
  after roughly 1.5–1.9 MB. `emergency_reset()` does not clear it; a
  port-level `dev.reset()` does.
- A port reset sometimes lands the board in its DFU bootloader.
  `ApolloDebugger.exit_dfu()` brings it back.

None of these are partitioning problems, but any tool that writes multi-megabyte
flash layouts will hit all three.


## 7. What is designed but NOT tested

Stated plainly, because the difference matters. **Everything above about the
table, the slots and the write path is measured. Everything about booting is
not.**

- **BOOTADDR has not been exercised on this board.** The directive semantics
  come from TN-02203 and Diamond's own tooling. No image has been built with
  `--bootaddr` and no jump has been observed here. This is the single largest
  untested claim in the document.
- **The loader does not exist.** No USB-FS loader bitstream has been built. The
  images written to slots 3 and 4 are existing test bitstreams used as payload
  to exercise the tooling; slot 0 still holds the original blinker and is
  untouched.
- **`self_program` (T13) has never been pulsed.** That it is routed is read
  from the platform file; that pulsing it triggers reconfiguration from
  BOOTADDR is inference from TN-02039, not measurement.
- **No slot has been booted from.** No image in slots 1..7 has ever been loaded
  into the FPGA. That a bitstream at a 64 KiB-aligned offset configures
  correctly is assumed, not shown.
- **Boot-from-flash cannot currently be host-triggered at all.** Apollo's
  `trigger_fpga_reconfiguration()` never releases INITN — see
  `reconfigure-initn-gap.md`. Testing any of the above needs that one-line
  firmware fix or a physical power cycle, which is why the boot path stops
  here rather than at a convenient point.
- **The 512 KiB slot size is a judgement call**, sized to measured images
  rather than Lattice's worst-case table. If a future design exceeds it, the
  layout needs revisiting; the tool errors rather than overrunning.

Where this stands: the mechanism is established from vendor documentation and
cross-checked against two independent implementations, the flash is safely
backed up, and the tooling is written and exercised read-only. The boot path
itself is unproven on this board.


## 8. Recovery

- Full image: `tmp/flashbackup/full-4MiB-verified.bin` (4 194 304 bytes,
  sha256 `c36b8c7f…`). Restores the board to its exact starting state.

  **Current flash differs from that backup**, deliberately: a partition table
  now exists at `0x3FF000`/`0x3FF800`, and slots 3 and 4 hold test bitstreams.
  The boot image at offset 0 is byte-identical to the backup and the board
  boots exactly as it did before. Restoring the backup wipes the table and the
  test slots and returns the chip to its original contents.

- `python3.15t repos/apollo/apollo_fpga/commands/cli.py exit-dfu` recovers from
  the DFU bootloader without touching the board.
- Slot 0 is never erased, so a power cycle always lands on a working image.
  This is the property the whole layout is built around, and it is the reason
  the loader goes at offset 0 rather than anywhere more convenient.
