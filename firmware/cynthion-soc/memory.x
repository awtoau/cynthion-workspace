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

/* Unwind tables, dropped rather than loaded into block RAM.
 *
 * `.eh_frame` describes how to unwind a stack. Nothing here can: the target's
 * panic strategy is abort, there is no unwinder in the binary, and the panic
 * handler prints and spins. riscv-rt's link.x never mentions the section, so lld
 * places it as an orphan immediately after .rodata -- inside RAM, counted against
 * the 32 KiB, and initialised into the bitstream. Measured at 1776 bytes on the
 * shell as it stands, which is 5% of its half of block RAM spent on a table with
 * no reader.
 *
 * DWARF is unaffected: `.debug_*` is not loaded, and `debug = true` in Cargo.toml
 * still gives an .elf that addr2line can read. */
SECTIONS
{
  /DISCARD/ : { *(.eh_frame) *(.eh_frame_hdr) }
}
