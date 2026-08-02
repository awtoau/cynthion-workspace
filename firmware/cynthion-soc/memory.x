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
 * The shell lives at the bottom and stays resident. Above it is a slot the `load`
 * command fills over the console, `scripts/soc_jtag_stage.py` fills over JTAG, and
 * `go` jumps to. That turns a firmware edit from a ~60 s bitstream rebuild into a
 * few seconds of typing.
 *
 * The payload is NOT position-independent, and does not need to be: it is linked
 * for PAYLOAD_ORIGIN, a fixed address we choose. Position-independent code would
 * only buy "load anywhere", and we control both linker scripts.
 *
 * 44K + 20K, not 32K + 32K. The even split was chosen when the two halves were
 * symmetric: one held a resident image, the other held an image being tried, and
 * neither had a reason to be the larger. They are no longer symmetric.
 *
 *   * The shell is what grows. It is one image with every command in it, and
 *     .text + .rodata + .bss is 33,248 bytes as this is written -- 480 bytes past
 *     what an even split allows, with no stack at all. Three separate changes have
 *     already been made to claw bytes back (opt-level "z", discarding .eh_frame),
 *     and they do not compose: each measured the slack the others had left.
 *   * The payload does not grow. `firmware/cynthion-payload` is the only image
 *     this tree builds for the slot and it is under 4 KiB. 20K is five times it.
 *
 * Costs nothing in gateware: the block RAM is 64 KiB either way (RAM_SIZE in
 * ecp5-test/riscv/vexii_hello_soc.py), the decoder sees one window, and no timing
 * path changes. It is a division of an address space, made in one file plus the
 * four places that must agree with it -- firmware/cynthion-payload/memory.x,
 * scripts/soc_payload.py, scripts/soc_jtag_stage.py and src/hyperram.rs, all of
 * which name the slot's size or its base.
 *
 * The shell's stack is what is left between .bss and _stack_start, so the 12 KiB
 * this buys is stack: measured at 2,820 bytes before it, which was the healthiest
 * any branch had managed.
 */
MEMORY
{
    RAM     : ORIGIN = 0x00000000, LENGTH = 44K
    PAYLOAD : ORIGIN = 0x0000b000, LENGTH = 20K
}

/* Pin the stack to the top of the SHELL region.
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
 * riscv-rt's link.x places it in REGION_RODATA, so on this design it was 1892 bytes of
 * the 32 KiB the shell then had -- and it comes out of the stack, because the stack is
 * whatever is left between .bss and _stack_start. Recovering it took the stack from
 * 928 bytes back to 2820, which is the measurement the 44K/20K split above is against.
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
