/* Cynthion r1.4 VexiiRiscv SoC memory map.
 *
 * Block RAM only, deliberately. The SoC does have flash memory-mapped at 0x10000000
 * with exe=1 so the I-cache can fetch from it, and moondancer's own linker script puts
 * .text there -- but code under test must not execute from the thing it is measuring.
 * Executing from flash while benchmarking flash times instruction fetch contending
 * with the reads, not the flash.
 *
 * Sizes must match ecp5-test/riscv/vexii_hello_soc.py: RAM_BASE and RAM_SIZE.
 */
MEMORY
{
    RAM : ORIGIN = 0x00000000, LENGTH = 64K
}

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
