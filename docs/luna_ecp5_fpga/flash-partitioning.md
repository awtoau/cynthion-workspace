# Flash partitioning and boot selection on ECP5

From Lattice primary sources — FPGA-TN-02039 rev 2.3, **FPGA-TN-02203** rev 1.8
— and the locally installed Diamond 3.14. Hardware-demonstrated where stated.

## How the boot image is actually selected

**BOOTADDR is a directive, not a fallback.** TN-02203 §7: the next target
address comes from the bitstream loaded during the *current* configuration, and
PROGRAMN or REFRESH jumps there unconditionally. Diamond's Deployment Tool
exposes it as "Next Pattern `<prim|alt1..alt4>`" — each image names its
successor.

That corrects the obvious guess. The **golden/dual-boot fallback is a separate
mechanism**, driven by a Jump command stored in flash rather than by BOOTADDR,
and TN-02039 §6.1.3 places the golden image at `0xFFFF00` — 16 MiB, off the end
of a 4 MiB part. **The automatic-fallback pattern is not available on this
board.**

**Build-time only.** BOOTADDR is 8 CRAM fuse bits, 64 KiB granular with 16 MiB
reach. Two independent sources agree: Trellis `ecppack.cpp:167-195`, and
Lattice's own `ispVM_023.xdf`, which lists eight `Multiboot_cfg_Address` fuses.
Worth noting because the Trellis fuzzer admits its field name is a guess — the
vendor database confirms it. There is no fabric path to the configuration
engine.

**r1.4 routes PROGRAMN to an FPGA pin** (`self_program`, T13), so a running
design can trigger the jump itself. That is what makes any of this viable.

## The design consequence

Selection cannot be expressed as data. "Boot slot 3 next" has to be baked into
a bitstream, because the address lives in fuses set at build time.

So for a bench selector, configuring over JTAG via Apollo is probably the better
answer. BOOTADDR earns its complexity only for **standalone** switching, where
no host is attached.

## Demonstrated on hardware

The full 4 MiB was backed up and verified first. Table init, list, write and
verify all round-trip through real flash. The locked-slot interlock refuses
slot 0. **Slot 0 was verified byte-identical after writing slots 3 and 4**,
confirming erases stay inside their slot.

The trap that motivates explicit lengths was reproduced deliberately: after a
partial overwrite, inferring the length reports **402 953 bytes where 99 963
were written**. A second independent reason for explicit lengths also surfaced
— bitstreams end in `0xff` padding, so inference undercounts even on a clean
write.

## A latent bug in the existing tooling

**A single `flash-read` of the full 4 MiB silently corrupts past about 1.9 MB**,
returning the READ_PAGE opcode and each page's own address in place of data.

It presents convincingly: it initially looked like 35 used blocks in the high
half of the chip. None was real, and trusting it would have produced a wrong
slot layout. `flash_backup.py` detects the pattern and retries.

Earlier reads in this project were 400 KB or less and so are unaffected.

## Current flash state

Deliberately no longer identical to the backup: a partition table sits at
`0x3FF000` and test bitstreams in slots 3 and 4. **The boot image at offset 0
is untouched** and the board boots as before. Restoring the backup reverts
everything.

## Untested

Stated plainly rather than implied: BOOTADDR itself, the loader (not built),
pulsing T13, and booting from any slot. Apollo's INITN gap blocks
host-triggered reconfiguration, so all of those need either the one-line
firmware fix or a physical power cycle.
