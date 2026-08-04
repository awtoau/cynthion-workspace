/* The smallest reset stub that lets the FreeRTOS skeleton link: set the stack,
 * zero .bss, call main. riscv-rt does this for the Rust skeletons; this is the
 * C equivalent, kept minimal so the comparison is of kernels and not of
 * runtimes.
 */
    .section .init, "ax"
    .global _start
_start:
    la      sp, _stack_start
    la      a0, __sbss
    la      a1, __ebss
1:  bgeu    a0, a1, 2f
    sw      zero, 0(a0)
    addi    a0, a0, 4
    j       1b
2:  call    main
3:  j       3b
