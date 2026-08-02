/* Where the resident bootloader lives, and where it hands over.
 *
 * One 64 KiB block RAM peripheral, cut in two by this file and by
 * firmware/cynthion-soc/memory.x. The cut is a linker fiction: the Wishbone decoder in
 * ecp5-test/riscv/vexii_hello_soc.py sees a single window at RAM_BASE, so the boundary
 * can be moved to any address without touching the gateware or the DP16KD geometry.
 *
 * | region | origin | length | contents                                    |
 * |--------|--------|--------|---------------------------------------------|
 * | BOOT   | 0x0000 | 1 KiB  | this image, and its stack                   |
 * | IMAGE  | 0x0400 | 63 KiB | whatever runs: the shell, or a staged image  |
 *
 * 1 KiB is sized from the measurement, not chosen: the image is 492 bytes and the
 * deepest call chain holds 80, so the region is a little over twice what is used. The
 * cut could be at 512 and the link would pass, with 22 bytes to spare -- which is not a
 * margin, it is a coincidence waiting to be broken by a codegen change.
 *
 * The boundary must match, in this order of authority:
 *   - IMAGE_ORIGIN in ecp5-test/riscv/vexii_hello_soc.py, which packs both images
 *   - RAM in firmware/cynthion-soc/memory.x
 *   - PAYLOAD in firmware/cynthion-payload/memory.x
 *   - MAX_IMAGE in firmware/cynthion-soc/src/hyperram.rs, which is IMAGE's length
 * `scripts/soc_generate_pac.py --check` reads all of them and fails on a disagreement.
 */
MEMORY
{
    BOOT  : ORIGIN = 0x00000000, LENGTH = 1K
    IMAGE : ORIGIN = 0x00000400, LENGTH = 63K
}

/* The CPU's reset vector, `reset_addr=RAM_BASE` in ecp5-test/riscv/vexii_cpu.py. */
ENTRY(_start)

SECTIONS
{
    /* `.start` first and at the very base, because 0x0 is the reset vector and
     * whatever byte lands there is the first instruction the CPU executes. */
    .start ORIGIN(BOOT) : { KEEP(*(.start)); } > BOOT

    .text   : { *(.text .text.*); }     > BOOT
    .rodata : { *(.rodata .rodata.*); } > BOOT

    /* Named so they can be asserted empty, not because anything is expected here.
     *
     * Nothing in this image copies initialisers or zeroes memory -- that is riscv-rt's
     * job and this crate does not use riscv-rt. A static arriving later would be read
     * at whatever value block RAM happened to initialise to, silently. The link fails
     * instead. */
    .data : { *(.data .data.*); } > BOOT
    .bss  : { *(.bss .bss.*); *(COMMON); } > BOOT

    _boot_end = .;

    /* Unwind tables, which nothing on this target unwinds. See
     * firmware/cynthion-soc/memory.x for the full argument. */
    /DISCARD/ : { *(.eh_frame) *(.eh_frame_hdr); }
}

ASSERT(SIZEOF(.data) == 0, "cynthion-boot has .data; nothing copies initialisers here")
ASSERT(SIZEOF(.bss) == 0, "cynthion-boot has .bss; nothing zeroes it here")

/* The stack, at the top of BOOT and growing down towards `.rodata`.
 *
 * Not at the top of block RAM: that is inside IMAGE, and a bootloader whose stack lived
 * in the region it copies into would overwrite its own return addresses partway through
 * the copy.
 *
 * The deepest path is `_start -> boot -> pass -> read_word`: 16 bytes of frame in
 * `boot` and 64 in `pass`, 80 in total, read out of the prologues. The image is 492
 * bytes, so 528 are free -- 6.6 times what is used.
 *
 * The floor below is 256: three times the measurement, rounded up. Not 512, and the
 * difference is what the check is for. A floor set just under what is free fires on the
 * next edit whatever that edit is, which trains people to raise it; one set at three
 * times the need fires when growth has taken more than half the room left, which is a
 * point at which widening BOOT is worth thinking about. What it catches either way is
 * code growing until the stack lands in `.rodata` -- which corrupts a constant rather
 * than faulting, and would present as a CRC that never matches. */
/* One word of boot status, at the very top of BOOT with the stack below it.
 *
 * Written on every boot, immediately before the jump: a mark and a reason code saying
 * which path was taken. It changes nothing -- see src/main.rs -- and exists so a board
 * that came up on the bitstream's image can be asked why.
 *
 * Here rather than in the image region because the image owns every byte of that and
 * would zero this one on its way up; here rather than in HyperRAM because HyperRAM is
 * the thing being reported on, and a status word stored in the part that may not be
 * answering is a status word that cannot report that. 0x3fc, and `target.rs` in the
 * shell names the same address -- `scripts/soc_generate_pac.py --check` holds them
 * together.
 */
_boot_status    = ORIGIN(BOOT) + LENGTH(BOOT) - 4;
_boot_stack_top = _boot_status;

ASSERT(_boot_stack_top - _boot_end >= 256,
       "cynthion-boot leaves under 256 bytes of stack; shrink it or widen BOOT")

/* Where the bootloader hands over, and the only jump target in the image. */
_image_start = ORIGIN(IMAGE);
