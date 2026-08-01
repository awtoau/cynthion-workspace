/* Linker layout for a payload image, loaded into RAM by the resident shell.
 *
 * The payload occupies the UPPER half of block RAM. The lower half holds the shell,
 * which stays live the whole time -- it is what received these bytes and jumped here,
 * and `reset` returns to it. So this must not place a single byte below 0x8000.
 *
 * Must match PAYLOAD in firmware/cynthion-soc/memory.x and PAYLOAD_SIZE in
 * scripts/soc_payload.py.
 */
/* We are entered by a jump from the shell, not by a reset vector, so there is no
 * _start. Naming the entry stub silences a linker warning and documents the contract. */
ENTRY(_payload_entry)

MEMORY
{
    PAYLOAD : ORIGIN = 0x00008000, LENGTH = 32K
}

SECTIONS
{
    /* .start FIRST and at the very base of the slot.
     *
     * The shell jumps to the slot's base address, not to a symbol -- it has no symbol
     * table for us. So whatever lands at 0x8000 is what executes. If the linker put
     * .text ahead of the entry stub, the jump would land in the middle of some
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
     * its _start is linked for 0x0 and would collide with the shell. So payloads must
     * not rely on zero-initialised statics; keep state in locals.
     */
    .bss : { *(.bss .bss.*); *(COMMON); } > PAYLOAD

    /DISCARD/ : { *(.eh_frame .eh_frame_hdr); }
}

/* The payload borrows the shell's stack, which is already set up and lives safely below
 * at the top of the shell half. Nothing here touches sp.
 */
