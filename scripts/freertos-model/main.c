/* The FreeRTOS skeleton, doing the same visible work as every Rust skeleton in
 * `firmware/cynthion-soc/src/bin/model_*.rs`: a PLIC front end, two sources,
 * one shared counter, an idle loop.
 *
 * Built by `scripts/soc_freertos_probe.py`. What is being measured is the
 * kernel, so the tasks do the minimum that still forces the kernel's paths to
 * link: a block, a wake from an ISR, and a mutex.
 */
#include "FreeRTOS.h"
#include "semphr.h"
#include "task.h"

/* The SoC's map, from `firmware/cynthion-soc/src/target.rs`. */
#define PLIC_BASE       0xf0003000UL
#define PLIC_ENABLE     ( PLIC_BASE + 0x00002000UL )
#define PLIC_THRESHOLD  ( PLIC_BASE + 0x00200000UL )
#define PLIC_CLAIM      ( PLIC_BASE + 0x00200004UL )
#define PLIC_PRIORITY   ( PLIC_BASE )

#define UART_IRQ        1UL
#define TYPE_C_IRQ      3UL

#define REG( a )    ( *( volatile uint32_t * ) ( a ) )

/* The shared counter every skeleton increments, behind the mutex, because a
 * mutex is what this model offers that the cooperative one does not. */
static volatile uint32_t ulServiced;
static SemaphoreHandle_t xServicedMutex;
static StaticSemaphore_t xServicedMutexBuffer;

/* One semaphore per source: the ISR gives, the task takes. FreeRTOS's answer
 * to `binds =`. */
static SemaphoreHandle_t xConsoleRx, xTypeC;
static StaticSemaphore_t xConsoleRxBuffer, xTypeCBuffer;

/* Per-task stacks and TCBs, statically allocated. THIS is the cost of the
 * model: one of each per task, sized for that task's deepest call chain, and
 * nothing recovers the slack. */
static StackType_t xConsoleStack[ configMINIMAL_STACK_SIZE ];
static StackType_t xTypeCStack[ configMINIMAL_STACK_SIZE ];
static StaticTask_t xConsoleTcb, xTypeCTcb;

static void prvService( SemaphoreHandle_t xSource, uint32_t ulIrq )
{
    for( ;; )
    {
        xSemaphoreTake( xSource, portMAX_DELAY );
        xSemaphoreTake( xServicedMutex, portMAX_DELAY );
        ulServiced++;
        xSemaphoreGive( xServicedMutex );
        /* Re-arm: the ISR masked the source so the level would drop. */
        REG( PLIC_ENABLE ) |= ( 1UL << ulIrq );
    }
}

static void prvConsoleRxTask( void * pvParameters )
{
    ( void ) pvParameters;
    prvService( xConsoleRx, UART_IRQ );
}

static void prvTypeCTask( void * pvParameters )
{
    ( void ) pvParameters;
    prvService( xTypeC, TYPE_C_IRQ );
}

/* The PLIC front end. FreeRTOS's RISC-V port calls this from its trap handler
 * for anything that is not the timer or the software interrupt, so it is the
 * same claim loop `src/irq.rs` has -- the kernel does not touch the PLIC any
 * more than RTIC's does. */
void freertos_risc_v_application_interrupt_handler( void )
{
    BaseType_t xHigherPriorityTaskWoken = pdFALSE;
    uint32_t ulSource;

    while( ( ulSource = REG( PLIC_CLAIM ) ) != 0UL )
    {
        if( ulSource == UART_IRQ )
        {
            REG( PLIC_CLAIM ) = ulSource;
            REG( PLIC_ENABLE ) &= ~( 1UL << ulSource );
            xSemaphoreGiveFromISR( xConsoleRx, &xHigherPriorityTaskWoken );
        }
        else if( ulSource == TYPE_C_IRQ )
        {
            REG( PLIC_CLAIM ) = ulSource;
            REG( PLIC_ENABLE ) &= ~( 1UL << ulSource );
            xSemaphoreGiveFromISR( xTypeC, &xHigherPriorityTaskWoken );
        }
        else
        {
            REG( PLIC_CLAIM ) = ulSource;
        }
    }

    portYIELD_FROM_ISR( xHigherPriorityTaskWoken );
}

/* Required by configSUPPORT_STATIC_ALLOCATION: the kernel asks the application
 * where to put the idle task, because it will not allocate. A third stack, and
 * it is not optional. */
void vApplicationGetIdleTaskMemory( StaticTask_t ** ppxIdleTaskTCBBuffer,
                                    StackType_t ** ppxIdleTaskStackBuffer,
                                    configSTACK_DEPTH_TYPE * puxIdleTaskStackSize )
{
    static StaticTask_t xIdleTcb;
    static StackType_t xIdleStack[ configMINIMAL_STACK_SIZE ];

    *ppxIdleTaskTCBBuffer = &xIdleTcb;
    *ppxIdleTaskStackBuffer = xIdleStack;
    *puxIdleTaskStackSize = configMINIMAL_STACK_SIZE;
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

    xServicedMutex = xSemaphoreCreateMutexStatic( &xServicedMutexBuffer );
    xConsoleRx = xSemaphoreCreateBinaryStatic( &xConsoleRxBuffer );
    xTypeC = xSemaphoreCreateBinaryStatic( &xTypeCBuffer );

    xTaskCreateStatic( prvConsoleRxTask, "rx", configMINIMAL_STACK_SIZE, NULL,
                       3, xConsoleStack, &xConsoleTcb );
    xTaskCreateStatic( prvTypeCTask, "tc", configMINIMAL_STACK_SIZE, NULL,
                       2, xTypeCStack, &xTypeCTcb );

    vTaskStartScheduler();

    for( ;; )
    {
    }
}

/* The three libc functions the kernel reaches for, defined here because there
 * is no rv32 libc beside this cross compiler. Byte at a time: they are called
 * once per task creation and once per name copy, and a fast version would be
 * adding somebody else's code to a measurement of the kernel. */
void * memset( void * s, int c, size_t n )
{
    unsigned char * p = s;

    while( n-- > 0U )
    {
        *p++ = ( unsigned char ) c;
    }

    return s;
}

void * memcpy( void * d, const void * s, size_t n )
{
    unsigned char * pd = d;
    const unsigned char * ps = s;

    while( n-- > 0U )
    {
        *pd++ = *ps++;
    }

    return d;
}

char * strncpy( char * d, const char * s, size_t n )
{
    char * start = d;

    while( n-- > 0U )
    {
        *d = *s;

        if( *s != '\0' )
        {
            s++;
        }

        d++;
    }

    return start;
}

/* What the probe reports as the per-task RAM cost, resolved by the compiler
 * rather than read off a header. */
const uint32_t ulSizeofStaticTask = ( uint32_t ) sizeof( StaticTask_t );
const uint32_t ulSizeofStaticQueue = ( uint32_t ) sizeof( StaticSemaphore_t );
const uint32_t ulMinimalStackBytes =
    ( uint32_t ) ( configMINIMAL_STACK_SIZE * sizeof( StackType_t ) );
