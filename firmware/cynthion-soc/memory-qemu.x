/* QEMU `-M virt` memory map -- the counterpart to memory.x.
 *
 * memory.x is the hardware one (RAM at 0x00000000, 32K + a 32K payload slot, matching
 * the block RAM in ecp5-test/riscv/vexii_hello_soc.py). This file exists because virt
 * puts DRAM at 0x80000000 and nothing whatsoever at 0, so the same image cannot be
 * linked for both. Selected by scripts/soc_test.py, which passes
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
    RAM     : ORIGIN = 0x80000000, LENGTH = 1M
    PAYLOAD : ORIGIN = 0x80100000, LENGTH = 32K
}

/* Roomier than the board on purpose, and only here.
 *
 * The hardware split is 32K+32K because that is how much block RAM exists. Under QEMU
 * the constraint is gone, and the firmware needs the slack: hyperram.rs's QEMU backend
 * stands the staging buffer up in .bss, which is 32 KiB the board keeps in an external
 * part. Sizing this to match the board would fail the link with a region overflow rather
 * than fail a test, so it would never have been a useful check.
 *
 * The payload slot keeps its 32K so `_payload_size` means the same thing on both, and
 * `go` reports a plausible address.
 */

/* Pin the stack to the top of the shell half, as on hardware: a `load` that grew down
 * into the live stack would corrupt it silently.
 */
_stack_start = ORIGIN(RAM) + LENGTH(RAM);

/* Exported so the shell knows where to write and where to jump. */
_payload_start = ORIGIN(PAYLOAD);
_payload_size  = LENGTH(PAYLOAD);

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
