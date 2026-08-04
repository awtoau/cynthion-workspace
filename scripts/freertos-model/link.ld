/* The same two regions `firmware/cynthion-soc/memory.x` gives the Rust
 * skeletons: .text and .rodata in flash, everything writable in the 63 KiB of
 * block RAM. Only the sections the FreeRTOS skeleton produces are placed --
 * this is a measurement, not an image anyone boots.
 */
OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY
{
    RAM   : ORIGIN = 0x00000400, LENGTH = 63K
    FLASH : ORIGIN = 0x100B0000, LENGTH = 0x350000
}

_stack_start = ORIGIN(RAM) + LENGTH(RAM);

SECTIONS
{
    .text : {
        *(.init)
        *(.text .text.*)
    } > FLASH

    .rodata : {
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
    } > FLASH

    .data : {
        *(.data .data.*)
        *(.sdata .sdata.*)
    } > RAM AT > FLASH

    .bss (NOLOAD) : {
        __sbss = .;
        *(.bss .bss.*)
        *(.sbss .sbss.*)
        *(COMMON)
        __ebss = .;
    } > RAM

    /* The interrupt stack, which the RISC-V port requires and which is a cost
     * of this model rather than of this skeleton: every trap switches to it, so
     * it must hold the deepest handler chain, and it is separate from -- and in
     * addition to -- every task stack. 1 KiB is a placeholder large enough to
     * link, not a measurement of what any handler needs. */
    .isr_stack (NOLOAD) : {
        . = ALIGN( 16 );
        . += 1024;
        __freertos_irq_stack_top = .;
    } > RAM

    /DISCARD/ : { *(.eh_frame) *(.eh_frame_hdr) *(.comment) }
}
