# `trigger_fpga_reconfiguration()` leaves INITN held low

Found while trying to measure quad-SPI boot time, which needs the FPGA to
configure itself from flash on command. It cannot, and the cause is in Apollo's
firmware rather than in the bitstream or the flash.

## The gap

`repos/apollo/firmware/src/boards/cynthion_d11/fpga.c:52-77` resets the JTAG
TAP, pulses `PROGRAMN`, and calls `fpga_set_online(true)`. It never touches
INITN.

INITN on the ECP5 is open drain and has to be released **high** for
configuration to proceed. On r1.4 it carries a pull-down to ground and no
pull-up, so nothing releases it unless something actively drives it. The only
thing that does is `permit_fpga_configuration(true)`, and that is called from
exactly two places, both in MCU startup:

    firmware/src/main.c:74
    firmware/src/main.c:81

So `fpga_set_online(true)` sets the software flag while the hardware stays
held. After a `force-offline`, the documented reconfigure-from-flash command
cannot complete.

## Why this is diagnosed rather than guessed

The ECP5 status register reads **Fail set with BSE error 0** -- configuration
was attempted and abandoned, not handed a bitstream it rejected. Corroborating:

- the same bitstream reads back byte-exact from flash
- the same bitstream loads fine over JTAG
- every host trigger fails identically with `INITN=0` --
  `REQUEST_RECONFIGURE`, `LSC_REFRESH`, `ISC_DISABLE`+refresh, USB reset

## Consequences

Boot-from-flash cannot be triggered from the host on this board, so anything
that needs to measure or use it needs a physical power cycle between attempts.

The fix looks small: call `permit_fpga_configuration(true)` from
`trigger_fpga_reconfiguration()` before pulsing PROGRAMN. Not attempted here --
it is upstream firmware and wants its own testing.

## Also established

`ecppack --freq` accepts only a fixed set of values; 50.0 and 100.0 are
rejected while 38.8 is accepted. The top of the set is 62.0 MHz, which is also
the top of Lattice's MCLK frequency table (FPGA-TN-02039), so ecppack cannot
request an out-of-spec *configuration* clock. That hazard applies to user-mode
flash reads, not to configuration.

`--spimode` does reach the bitstream: each mode inserts a 4-byte `0x79`
SPI_MODE command after the preamble -- `fast-read` gives `79 49`, `dual-spi`
`79 51`, `qspi` `79 59`, and the baseline inserts nothing.

Passing it through Amaranth needs a workaround: `CynthionPlatform` hardcodes
`ecppack_opts` and forwards it alongside `**kwargs`, so `build(ecppack_opts=…)`
raises `TypeError`, and subclassing does not help because the hardcoding sits
below `CynthionPlatformRev1D4`.

## Still unmeasured

Whether quad-SPI boot is actually faster. The build side works and ten variants
build and verify clean; the timing needs the INITN gap resolved or a power
cycle between variants.


## What is in flash now, and a trap in reading it

The board currently boots the `baseline-38.8` blinker the boot-timing work
built -- 100336 bytes, byte-for-byte identical to
`tmp/qspiboot/baseline-38.8/top.bit`. That is what the LEDs are doing.

The trap: flash beyond 100336 bytes is **not erased**. It holds the tail of a
larger bitstream that the smaller one only partly overwrote. Reading flash and
scanning back from the end for the first non-`0xff` byte therefore reports
248515 bytes, and no bitstream of that size ever existed.

That measurement sent an identification attempt badly wrong -- it was compared
against designs of a similar apparent size, none of which could match, and the
conclusion drawn was that the image predated the session. Comparing bitstream
*bodies* rather than sizes found the real answer immediately.

So: identify a flash image by comparing content from a fixed offset, never by
inferring its length from where the erased region begins. Programming a smaller
bitstream over a larger one leaves the difference behind.

## Checking the "no fabric path to configuration" claim against Diamond

That claim originally rested on the open tooling's primitive list plus a
supporting argument: that MachXO2 has `PCNTR` for exactly this and the ECP5
does not, so the absence is deliberate rather than an oversight.

**The supporting argument was wrong.** Lattice Diamond 3.14 is installed
locally, and its simulation libraries are the vendor's own declaration of what
each family exposes. Comparing them:

    ecp5u    171 primitives
    machxo2  194 primitives

The 23 MachXO2 has that ECP5 does not are arithmetic comparators (`AGEB2`,
`ALEB2`, `ANEB2`), block RAM variants (`DP8KC`, `SP8KC`, `PDPW8KC`, `FIFO8KB`),
DDR I/O, PLLs and flip-flop variants. **`PCNTR` is not among them, and does not
appear in the MachXO2 library at all.**

So the correct statement is that *neither* family exposes a configuration-access
primitive in Diamond's simulation libraries, not that ECP5 uniquely lacks one.

The ECP5's config-adjacent primitives, from Lattice's own library:

| primitive | function |
|---|---|
| `DTR` | die temperature register |
| `GSR`, `SGSR` | global set/reset |
| `OSCG` | internal oscillator |
| `SEDGA` | soft error detection |
| `USRMCLK` | the configuration clock pin |
| `EXTREFB` | SERDES reference clock |
| `SRAMWB` | slice write-data/address mux for distributed LUT RAM |

`SRAMWB` is worth noting because the name suggests SRAM with a Wishbone
interface. Reading it, its ports are `WDO0..3` and `WADO0..3` — write data and
write address out, inside a logic slice. It is routing, not configuration.

Diamond also ships no ECP5 configuration-access IP: the EFB-style hits under
`data/ptmdata/` are for other families (LASC, LMS).

### How strong this is

Strong evidence, not proof. Simulation libraries describe what Diamond
simulates, and a hardened block reachable only through a soft IP core need not
appear as a standalone primitive — MachXO2's EFB has configuration access on
real silicon without being a primitive in this directory. What can be said is
that for ECP5, neither the primitive library nor the shipped IP offers such a
path.
