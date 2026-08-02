/* Cynthion r1.4 VexiiRiscv SoC memory map.
 *
 * Block RAM only, deliberately. The SoC does have flash memory-mapped at 0x10000000
 * with exe=1 so the I-cache can fetch from it, and moondancer's own linker script puts
 * .text there -- but code under test must not execute from the thing it is measuring.
 * Executing from flash while benchmarking flash times instruction fetch contending
 * with the reads, not the flash.
 *
 * Sizes must match ecp5-test/riscv/vexii_hello_soc.py: RAM_BASE and RAM_SIZE.
 *
 * This is the HARDWARE script, named `memory.x` because that is what the `-Tmemory.x` in
 * .cargo/config.toml asks for, so it is what every plain `cargo build` uses. Its
 * counterpart is memory-qemu.x, selected only by scripts/soc_test.py. Keep the exported
 * symbols (_stack_start, _payload_start, _payload_size, _stext) identical in both: the
 * firmware reads them from the linker precisely so it does not have to know which target
 * it is on.
 */
/* RAM is split in two so the shell can load and run a second image without a
 * gateware rebuild.
 *
 * The shell lives at the bottom and stays resident. Above it is the payload slot:
 * where `try_boot` COPIES a staged image once it has verified its CRC, and where
 * `go` jumps. That turns a firmware edit from a ~60 s bitstream rebuild into a few
 * seconds of typing.
 *
 * Nothing writes the slot directly. An image arrives in HyperRAM first -- over the
 * console with `load`, or over JTAG with `scripts/soc_jtag_stage.py` while the CPU
 * is held in reset -- and only the bootloader moves it from there into block RAM.
 * That indirection is the architecture and not an accident of the implementation:
 * block RAM cannot stage its own replacement, because the shell is executing out of
 * it while the bytes arrive, and HyperRAM is an external part that survives the
 * reset in between. `src/hyperram.rs` holds the header layout both ends agree on.
 *
 * The payload is NOT position-independent, and does not need to be: it is linked
 * for PAYLOAD_ORIGIN, a fixed address we choose. Position-independent code would
 * only buy "load anywhere", and we control both linker scripts.
 *
 * Both halves are 32K, which is where this started and where it remains. The shell
 * has outgrown it -- see the note at the end of this file -- and moving the boundary
 * was considered and rejected: the shell is the resident image, so what grows is what
 * is pinned at 0x0, and dividing the same 64 KiB differently only postpones the
 * question. The answer is a bootloader that does not have to keep the growing image
 * resident, and that is a restructure rather than a linker script.
 */
MEMORY
{
    RAM     : ORIGIN = 0x00000000, LENGTH = 32K
    PAYLOAD : ORIGIN = 0x00008000, LENGTH = 32K
}

/* Pin the stack to the top of the SHELL half.
 *
 * riscv-rt defaults _stack_start to the end of REGION_STACK, which before the split
 * was the end of all 64K -- i.e. the top of what is now the payload slot. Loading an
 * image would have grown down into the live stack and corrupted it silently: the
 * bytes land, `go` jumps, and the fault appears somewhere unrelated.
 */
_stack_start = ORIGIN(RAM) + LENGTH(RAM);

/* Exported so the shell knows where to write and where to jump. */
_payload_start = ORIGIN(PAYLOAD);
_payload_size  = LENGTH(PAYLOAD);

/* riscv-rt 0.18 region aliases. All in RAM for the reason above. */
REGION_ALIAS("REGION_TEXT",   RAM);
REGION_ALIAS("REGION_RODATA", RAM);
REGION_ALIAS("REGION_DATA",   RAM);
REGION_ALIAS("REGION_BSS",    RAM);
REGION_ALIAS("REGION_HEAP",   RAM);
REGION_ALIAS("REGION_STACK",  RAM);

/* The CPU's reset vector, set in vexii_cpu.py as reset_addr=RAM_BASE. The two must
 * agree: a mismatch gives a CPU that fetches from an address nothing answers, which
 * looks exactly like a dead core. */
_stext = ORIGIN(REGION_TEXT);

/* Unwind tables, which nothing on this target unwinds. 1892 bytes, measured.
 *
 * `.eh_frame` describes how to restore registers while a stack is being unwound by a
 * panic. riscv32imac-unknown-none-elf has panic_strategy=abort, this crate's
 * `#[panic_handler]` diverges, and nothing links `_Unwind_*` -- so the table is
 * described and never read. It arrives anyway, from the precompiled `core` and
 * `compiler_builtins` rlibs, which is why `-C force-unwind-tables=no` on this crate
 * does not remove it.
 *
 * riscv-rt's link.x places it in REGION_RODATA, so on this design it is 1892 bytes of
 * the 32 KiB the shell gets -- and it comes out of the stack, because the stack is
 * whatever is left between .bss and _stack_start. Recovering it took the stack from
 * 928 bytes back to 2820.
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

/* WHAT THIS DOES NOT CURRENTLY HOLD, measured 2026-08-02.
 *
 * With every command merged -- `board`, `info`, `selftest`, `time` -- the shell does
 * not link against the 32K above. lld reports the sections ending at 0x8ea4:
 *
 *     .text     25,496
 *     .rodata    9,584   (plus 22 bytes of .init.rust)
 *     .data           0
 *     .bss       1,412
 *     ------------------
 *     total     36,514   against 32,768: over by 3,748 bytes, and no stack at all
 *
 * That number is recorded rather than worked around. The two reclamations that are
 * in this tree -- discarding .eh_frame above, and `opt-level = "z"` in Cargo.toml --
 * are genuine deletions of waste and stay whatever happens next; three separate
 * branches each measured the slack they left (1292, 2820, 3592 bytes) and those were
 * three measurements of the same space, not three savings that add.
 *
 * Widening this region is not the fix. The shell is the RESIDENT image, so the thing
 * that grows is the thing pinned at 0x0, and the slot that does not grow got the
 * other half; any division of the same 64 KiB buys one more command. Inverting that
 * -- a bootloader that does not have to keep the growing image resident -- is filed
 * separately.
 *
 * Until then a board image needs a build that leaves something out. `--features qemu`
 * still links, because memory-qemu.x has a megabyte, so scripts/soc_test.py and every
 * simulation continue to run against the merged firmware.
 */
