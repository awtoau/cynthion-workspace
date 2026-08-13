/* Linker layout for a payload image: an alternative to the shell, not an addition.
 *
 * The bootloader at 0x0 copies whatever is staged in HyperRAM into the image region and
 * jumps to its base. A payload therefore REPLACES the shell rather than running
 * alongside it, and there is no resident code left to return to -- `reset` means the
 * bootloader, and the bootloader boots this again until
 * `scripts/soc_jtag_stage.py --clear` removes the header.
 *
 * Must match RAM in firmware/cynthion-soc/memory.x, IMAGE in
 * firmware/cynthion-boot/memory.x, and PAYLOAD_SIZE in scripts/soc_payload.py.
 */
/* We are entered by a jump from the bootloader, not by a reset vector, so there is no
 * _start. Naming the entry stub silences a linker warning and documents the contract. */
ENTRY(_payload_entry)

MEMORY
{
    PAYLOAD : ORIGIN = 0x00000400, LENGTH = 47K
}

/* The stack, at the top of block RAM, set by the entry stub.
 *
 * Nothing sets it for us any more. The bootloader hands over with `sp` pointing into
 * its own kilobyte at the bottom of block RAM, and a payload that pushed there would
 * overwrite the code that had just jumped to it -- harmless while running, and exactly
 * the sort of thing that is not harmless the next time this file changes.
 */
_stack_start = ORIGIN(PAYLOAD) + LENGTH(PAYLOAD);

SECTIONS
{
    /* .start FIRST and at the very base of the region.
     *
     * The bootloader jumps to the region's base address, not to a symbol -- it has no
     * symbol table for us. So whatever lands at 0x400 is what executes. If the linker
     * put .text ahead of the entry stub, the jump would land in the middle of some
     * arbitrary function.
     */
    .start ORIGIN(PAYLOAD) :
    {
        KEEP(*(.start));
    } > PAYLOAD

    .text   : { *(.text .text.*); }   > PAYLOAD
    .rodata : { *(.rodata .rodata.*); } > PAYLOAD
    .data   : { *(.data .data.*); }   > PAYLOAD

    /* .bss is NOT zeroed by anything here.
     *
     * riscv-rt normally does that, and this image deliberately does not use riscv-rt --
     * it would bring a runtime this file already replaces. So payloads must not rely on
     * zero-initialised statics; keep state in locals.
     */
    .bss : { *(.bss .bss.*); *(COMMON); } > PAYLOAD

    /DISCARD/ : { *(.eh_frame .eh_frame_hdr); }
}
