/* QEMU `-M virt` memory map -- the counterpart to memory.x.
 *
 * memory.x is the hardware one (this image at 0x00000400, above the 1 KiB bootloader,
 * matching the block RAM in ecp5-test/riscv/vexii_hello_soc.py). This file exists
 * because virt puts DRAM at 0x80000000 and nothing whatsoever at 0, so the same image
 * cannot be linked for both. Selected by scripts/soc_test.py, which passes
 *
 *     CARGO_TARGET_RISCV32IMAC_UNKNOWN_NONE_ELF_RUSTFLAGS=
 *         "-C link-arg=-Tmemory-qemu.x -C link-arg=-Tlink.x"
 *
 * in place of the rustflags in .cargo/config.toml. Nothing else in the build differs;
 * see src/target.rs for the peripheral side -- the console driver itself (src/uart.rs)
 * is shared, because both targets present a standard NS16550A.
 *
 * Addresses come from QEMU's own device tree, not from documentation:
 *
 *     qemu-system-riscv32 -M virt -machine dumpdtb=tmp/virt.dtb -display none
 *     dtc -I dtb -O dts tmp/virt.dtb    ->  memory@80000000, serial@10000000
 */
MEMORY
{
    RAM : ORIGIN = 0x80000000, LENGTH = 1M
}

/* Roomier than the board on purpose, and only here.
 *
 * The board's image region is 63 KiB because that is what is left of block RAM once the
 * bootloader has its kilobyte. Under QEMU the constraint is gone, and the firmware needs
 * the slack: hyperram.rs's QEMU backend stands the staging buffer up in .bss, which is
 * another 63 KiB the board keeps in an external part. Sizing this to match the board
 * would fail the link with a region overflow rather than fail a test, so it would never
 * have been a useful check.
 *
 * There is no BOOT region here and no second image. `-M virt` jumps straight to the ELF
 * entry point, and there is no block RAM at 0 to fall back into, so this machine has
 * exactly one image and it is this one. What that costs the gate is stated at the end
 * of this file.
 */

/* Pin the stack to the top of the region, as on hardware. */
_stack_start = ORIGIN(RAM) + LENGTH(RAM);

/* Must exist on both targets: `info` reads it to size the writable-memory budget, and
 * the firmware reads linker symbols precisely so it need not know which target it is on.
 * See the counterpart in memory.x. */
_ram_start = ORIGIN(RAM);

/* Where `reset` and `load` jump. On the board this is the bootloader at 0x0; here there
 * is no bootloader and nothing at 0, so it is this image's own entry point. `reset`
 * restarts the shell, which is what it means on a machine with one image. */
_reset_vector = ORIGIN(RAM);

/* riscv-rt 0.18 region aliases. */
REGION_ALIAS("REGION_TEXT",   RAM);
REGION_ALIAS("REGION_RODATA", RAM);
REGION_ALIAS("REGION_DATA",   RAM);
REGION_ALIAS("REGION_BSS",    RAM);
REGION_ALIAS("REGION_HEAP",   RAM);
REGION_ALIAS("REGION_STACK",  RAM);

/* Where the machine starts fetching. QEMU's `virt` reset stub jumps to the ELF entry
 * point, so this only has to agree with where .text was placed -- but a mismatch would
 * present exactly as it does on the board: a CPU fetching from an address nothing
 * answers, indistinguishable from a dead core. */
_stext = ORIGIN(REGION_TEXT);

/* An alias riscv-rt does not export and RTIC 2.3.0 asks for. See memory.x. */
PROVIDE(_ebss = __ebss);

/* Unwind tables, which nothing on this target unwinds. See memory.x for the full
 * argument and the measurement.
 *
 * This machine has 64 MiB and does not need the space. It is discarded here anyway,
 * because the value of scripts/soc_test.py rests on the two builds being the same
 * program -- a section present in one image and absent from the other is a difference
 * in what is being tested, arrived at for no reason. */
SECTIONS
{
    /DISCARD/ : { *(.eh_frame) *(.eh_frame_hdr) }
}

/* WHAT THE GATE STILL COVERS, now that the bootloader is a separate image.
 *
 * `scripts/soc_test.py` runs THIS crate, and this crate is where the shell, the
 * commands and the drivers are -- so the gate covers more of the product than it did,
 * not less: everything that used to be squeezed out of a board build for space is in
 * the image it tests.
 *
 * The staging code is shared source. `src/hyperram.rs` is compiled into
 * `firmware/cynthion-boot` as a module rather than copied, so the header ordering, the
 * magic, the length bound and the CRC polynomial the gate exercises through `load` are
 * the same lines the bootloader runs.
 *
 * What it cannot cover, and could not before: the bootloader itself. There is no
 * bootloader image on this machine, `virt` has no HyperRAM, and the QEMU backend's
 * stand-in lives in `.bss` and is zeroed by the reboot at the end of `load` -- so a
 * staged image never boots here. Verifying the round trip, the fallback and a failed
 * CRC needs the board.
 */
