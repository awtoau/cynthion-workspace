/* Cynthion r1.4 VexiiRiscv SoC memory map.
 *
 * Stage one keeps executable and writable sections in block RAM, but reads .rodata
 * from flash. Flash offset 0 holds the FPGA configuration; 0xb0000 is the established
 * Moondancer firmware offset and leaves that image intact.
 *
 * Sizes must match ecp5-test/riscv/soc/top.py: RAM_BASE, RAM_SIZE, IMAGE_ORIGIN.
 *
 * This is the HARDWARE script, named `memory.x` because that is what the `-Tmemory.x` in
 * .cargo/config.toml asks for, so it is what every plain `cargo build` uses. Its
 * counterpart is memory-qemu.x, selected only by scripts/soc_test.py. Keep the exported
 * symbols (_stack_start, _reset_vector, _stext) identical in both: the firmware reads
 * them from the linker precisely so it does not have to know which target it is on.
 */
/* This crate is an IMAGE, not the resident half.
 *
 * `firmware/cynthion-boot` is what sits at 0x0 and what the CPU's reset vector points
 * at. It is 492 bytes: read the staging header out of HyperRAM, checksum the image on
 * the way out, copy it here, jump here. Everything else -- this shell, every command,
 * every driver -- lives above it and is replaceable in seconds without a bitstream
 * rebuild.
 *
 * That is the inversion the split needed. Previously this crate was resident, so the
 * thing that grows was pinned at 0x0 and the thing that does not grow got the other
 * 32 KiB; the shell reached 36,514 bytes against 32,768 and stopped linking. Now the
 * growing image gets 63 KiB and the resident one changes rarely.
 *
 * | region | origin     | length   | contents                              |
 * |--------|------------|----------|---------------------------------------|
 * | BOOT   | 0x00000000 | 1 KiB    | cynthion-boot, and its stack          |
 * | RAM    | 0x00000400 | 63 KiB   | .data, .bss, stack                    |
 * | FLASH  | 0x100b0000 | 3392 KiB | .text, .rodata                        |
 *
 * The bitstream initialises block RAM only, and since `.text` moved to flash it carries
 * the bootloader at 0x0 and NOTHING ELSE -- the block RAM artifact is 0 bytes. The whole
 * image is programmed into flash separately, which is why a firmware change no longer
 * needs a bitstream rebuild (`./dev.py fw`, ~6 s).
 *
 * HyperRAM staging still replaces only the block-RAM image. Its flat image format has
 * no way to install the flash segment, so `.rodata` must already be present in flash.
 *
 * Images are NOT position-independent and do not need to be: they are linked for the
 * image origin, a fixed address we choose. Position-independent code would only buy
 * "load anywhere", and we control every linker script involved.
 */
MEMORY
{
    RAM   : ORIGIN = 0x00000400, LENGTH = 63K
    FLASH : ORIGIN = 0x100B0000, LENGTH = 0x350000
}

/* The stack, at the top of block RAM.
 *
 * riscv-rt defaults _stack_start to the end of REGION_STACK, which is this. Nothing
 * lives above it now that the payload slot is gone -- an image IS the upper region --
 * so the stack gets everything between .bss and 0x10000.
 */
_stack_start = ORIGIN(RAM) + LENGTH(RAM);

/* The base of writable memory, for `info` to size the block RAM budget against.
 *
 * It used to measure the budget as `_stack_start - __stext`, which was right only while
 * `.text` was the lowest thing in RAM. With `.text` in flash that subtraction spans two
 * address spaces and reported "54856 free of 4025876480". A region has an origin; take
 * it from the linker rather than inferring it from whatever happens to be lowest. */
_ram_start = ORIGIN(RAM);

/* Where `reset` and `load` jump, and the reason it is a symbol rather than a literal.
 *
 * On the board this is the bootloader at 0x0, so a reboot re-reads the staging header
 * and a `load` that has just written one boots into it. Under QEMU there is no
 * bootloader and nothing at 0, so it is this image's own entry point; `reset` restarts
 * the shell there, which is what it means on a machine with one image.
 */
_reset_vector = 0x00000000;

/* Stage two: code joins immutable data in flash.
 *
 * `.text` here is what the whole split was for. Block RAM keeps only what must be
 * WRITABLE -- .data, .bss and the stack -- so the 63 KiB stops being a budget the
 * shell competes with itself for, and the image can grow into 3392 KiB of flash.
 *
 * This requires FLASH_CACHED in ecp5-test/riscv/soc/top.py. `exe=0` forbids
 * instruction fetch from the window, so the uncached configuration cannot run code from
 * here at all; the two settings are one decision. */
REGION_ALIAS("REGION_TEXT",   FLASH);
REGION_ALIAS("REGION_RODATA", FLASH);
REGION_ALIAS("REGION_DATA",   RAM);
REGION_ALIAS("REGION_BSS",    RAM);
REGION_ALIAS("REGION_HEAP",   RAM);
REGION_ALIAS("REGION_STACK",  RAM);

/* Where the bootloader jumps, which is the base of this region.
 *
 * riscv-rt's link.x places `.init` -- and `_start` with it -- at `_stext`, so the first
 * instruction of this image is the first byte of the region. The bootloader jumps to an
 * ADDRESS, not to a symbol; it has no symbol table for us. */
_stext = ORIGIN(REGION_TEXT);

/* An alias riscv-rt does not export and RTIC 2.3.0 asks for.
 *
 * RTIC's RISC-V backend emits a stack-overflow check against `_ebss`. riscv-rt has
 * called it `__ebss` since 0.12; nothing in 0.18's link.x provides the single-underscore
 * spelling, so `--features rtic` fails at LINK, after a clean compile, with `undefined
 * symbol: _ebss` and a `did you mean: __ebss` hint. An upstream mismatch, not a choice
 * either project made.
 *
 * PROVIDE, so this defines the symbol only when something references it: the shipping
 * image does not, and does not change. Delete it when RTIC catches up. */
PROVIDE(_ebss = __ebss);

/* Unwind tables, which nothing on this target unwinds. 1892 bytes, measured.
 *
 * `.eh_frame` describes how to restore registers while a stack is being unwound by a
 * panic. riscv32imac-unknown-none-elf has panic_strategy=abort, this crate's
 * `#[panic_handler]` diverges, and nothing links `_Unwind_*` -- so the table is
 * described and never read. It arrives anyway, from the precompiled `core` and
 * `compiler_builtins` rlibs, which is why `-C force-unwind-tables=no` on this crate
 * does not remove it.
 *
 * riscv-rt's link.x places it in REGION_RODATA, so it follows `.rodata` to flash.
 *
 * A debugger loses nothing: `debug = true` in Cargo.toml emits DWARF `.debug_frame`,
 * which is not loaded and is what gdb uses here anyway.
 *
 * Must stay identical in memory-qemu.x. A section discarded on one target and kept on
 * the other means the two builds no longer have the same layout, and scripts/soc_test.py
 * is only evidence about the board while they do. */
SECTIONS
{
    /DISCARD/ : { *(.eh_frame) *(.eh_frame_hdr) }
}

/* The stack must actually fit, and nothing checked that until it did not.
 *
 * The stack starts at the top of this region and grows DOWN into whatever is
 * left above .bss. That is not a reserved area -- it is a remainder -- so every
 * byte of .text or .bss takes one from it, silently, until the two meet.
 *
 * When they met, the symptom was not a link error. It was `RINGS` in .bss being
 * overwritten by stack frames, so the receive ring's head and tail came back as
 * stack ADDRESSES and the firmware panicked with `index out of bounds: the len
 * is 256 but the index is 64016`. Measured at the time: 396 bytes of stack.
 *
 * 8 KiB is a floor, not a measurement of what this firmware needs -- riscv-rt
 * gives no stack-usage figure and nothing here computes one. It is chosen to be
 * comfortably more than the deepest call chain the shell has (formatting into a
 * UART inside a command inside the main loop) and to fail long before the ring
 * corruption does. Raise it if a real measurement ever says so; do not lower it
 * to make a build fit.
 */
_min_stack = 8K;
ASSERT(__ebss < ORIGIN(RAM) + LENGTH(RAM) - _min_stack,
       "IMAGE TOO BIG: .bss reaches within _min_stack of the stack top. The
        stack grows down into .bss and will corrupt it at runtime rather than
        failing here. Shrink .text/.bss, or move something out of this
        image.")
