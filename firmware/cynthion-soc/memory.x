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
 * The shell lives in the low half and stays resident. The high half is a slot the
 * `load` command fills over the console and `go` jumps to. That turns a firmware
 * edit from a ~60 s bitstream rebuild into a few seconds of typing.
 *
 * The payload is NOT position-independent, and does not need to be: it is linked
 * for PAYLOAD_ORIGIN, a fixed address we choose. Position-independent code would
 * only buy "load anywhere", and we control both linker scripts.
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
