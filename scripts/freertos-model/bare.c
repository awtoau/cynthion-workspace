/* The C floor: the same visible work as `main.c` with no kernel under it.
 *
 * `firmware/cynthion-soc/src/bin/model_bare.rs` is the Rust floor, and the two
 * exist for the same reason -- a `.text` figure for a kernel is only meaningful
 * as a difference from a build of the same code in the same language with the
 * same compiler and no kernel at all.
 */
#include <stdint.h>

#define PLIC_BASE      0xf0003000UL
#define PLIC_ENABLE    ( PLIC_BASE + 0x00002000UL )
#define PLIC_THRESHOLD ( PLIC_BASE + 0x00200000UL )
#define PLIC_CLAIM     ( PLIC_BASE + 0x00200004UL )
#define PLIC_PRIORITY  ( PLIC_BASE )

#define UART_IRQ       1UL
#define TYPE_C_IRQ     3UL

#define REG( a )    ( *( volatile uint32_t * ) ( a ) )

static volatile uint32_t ulServiced;

void trap_handler( void ) __attribute__( ( interrupt ) );

void trap_handler( void )
{
    uint32_t ulSource;

    while( ( ulSource = REG( PLIC_CLAIM ) ) != 0UL )
    {
        if( ( ulSource == UART_IRQ ) || ( ulSource == TYPE_C_IRQ ) )
        {
            ulServiced++;
        }

        REG( PLIC_CLAIM ) = ulSource;
    }
}

int main( void )
{
    uint32_t ulSource;

    REG( PLIC_THRESHOLD ) = 0UL;

    for( ulSource = UART_IRQ; ulSource <= TYPE_C_IRQ; ulSource++ )
    {
        REG( PLIC_PRIORITY + 4UL * ulSource ) = 1UL;
        REG( PLIC_ENABLE ) |= ( 1UL << ulSource );
        REG( PLIC_CLAIM ) = ulSource;
    }

    /* `mtvec` from a C operand rather than from `la` inside the asm: with LTO
     * the only reference to the handler would otherwise be a string the
     * compiler cannot see, and the linker drops it. */
    __asm__ volatile ( "csrw mtvec, %0\n"
                       "li t0, 0x800\ncsrs mie, t0\n"
                       "li t0, 0x8\ncsrs mstatus, t0"
                       :: "r" ( trap_handler ) : "t0" );

    for( ;; )
    {
    }
}
