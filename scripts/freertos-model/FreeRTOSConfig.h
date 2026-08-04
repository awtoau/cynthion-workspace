/* FreeRTOS for our VexiiRiscv SoC, configured for the smallest kernel that
 * would still do this firmware's job. `scripts/soc_freertos_probe.py` builds it
 * and `docs/soc-concurrency-models.md` is what the numbers are for.
 *
 * Every option below is turned OFF unless something in `firmware/cynthion-soc`
 * actually needs it. That is deliberate: the question is what a FreeRTOS-style
 * 1 ms tick master costs at BEST on this machine, and a default config would
 * answer a different one.
 */
#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

/* The SoC's clock. `ecp5-test/riscv/vexii_hello_soc.py`, and `TIME_HZ` in
 * `firmware/cynthion-soc/src/target.rs`. */
#define configCPU_CLOCK_HZ                      72000000

/* The CLINT, at the address `src/target.rs` gives. `mtime` is read-only here
 * and `mtimecmp` is the one comparator `ecp5-test/riscv/vexii_clint.py` has --
 * which is exactly the register FreeRTOS's RISC-V port drives its tick from. */
#define configMTIME_BASE_ADDRESS                0xf0004000UL + 0xbff8UL
#define configMTIMECMP_BASE_ADDRESS             0xf0004000UL + 0x4000UL

/* The owner's suggestion, and FreeRTOS's own idiom. Same period as
 * `timer::PERIOD_MS`, so the tick rate is not what differs between the two. */
#define configTICK_RATE_HZ                      1000

#define configUSE_PREEMPTION                    1
#define configUSE_TIME_SLICING                  1
#define configUSE_PORT_OPTIMISED_TASK_SELECTION 0
#define configMAX_PRIORITIES                    4
#define configMINIMAL_STACK_SIZE                128
#define configMAX_TASK_NAME_LEN                 8
#define configUSE_16_BIT_TICKS                  0
#define configIDLE_SHOULD_YIELD                 1

/* Static only. The heap is the first thing to go: `firmware/cynthion-soc` has
 * no allocator at all, and a model that needed one would be answering for
 * `heap_4.c` as well as for the kernel. Every stack below is a `static`. */
#define configSUPPORT_STATIC_ALLOCATION         1
#define configSUPPORT_DYNAMIC_ALLOCATION        0

/* What this firmware would actually use. A mutex because `src/bus.rs` exists to
 * give the one I2C controller a single owner, and a binary semaphore because
 * that is how a handler hands a byte to a task. */
#define configUSE_MUTEXES                       1
#define configUSE_COUNTING_SEMAPHORES           0
#define configUSE_RECURSIVE_MUTEXES             0
#define configUSE_QUEUE_SETS                    0

/* Off. The software timer service is a task, a queue and a list of its own --
 * and the whole question this measurement serves is whether periodic work
 * should be scheduled in hardware instead. */
#define configUSE_TIMERS                        0

#define configUSE_IDLE_HOOK                     0
#define configUSE_TICK_HOOK                     0
#define configUSE_MALLOC_FAILED_HOOK            0
#define configCHECK_FOR_STACK_OVERFLOW          0
#define configUSE_TRACE_FACILITY                0
#define configGENERATE_RUN_TIME_STATS           0
#define configUSE_STATS_FORMATTING_FUNCTIONS    0
#define configUSE_CO_ROUTINES                   0
#define configUSE_TASK_NOTIFICATIONS            1
#define configTASK_NOTIFICATION_ARRAY_ENTRIES   1
#define configUSE_APPLICATION_TASK_TAG          0
#define configUSE_NEWLIB_REENTRANT              0
#define configUSE_TICKLESS_IDLE                 0
#define configRECORD_STACK_HIGH_ADDRESS         0

#define INCLUDE_vTaskPrioritySet                0
#define INCLUDE_uxTaskPriorityGet               0
#define INCLUDE_vTaskDelete                     0
#define INCLUDE_vTaskSuspend                    1
#define INCLUDE_xTaskDelayUntil                 1
#define INCLUDE_vTaskDelay                      1
#define INCLUDE_xTaskGetSchedulerState          0
#define INCLUDE_xTaskGetCurrentTaskHandle       0
#define INCLUDE_uxTaskGetStackHighWaterMark     0
#define INCLUDE_xTaskGetIdleTaskHandle          0
#define INCLUDE_eTaskGetState                   0
#define INCLUDE_xTimerPendFunctionCall          0
#define INCLUDE_xTaskAbortDelay                 0
#define INCLUDE_xSemaphoreGetMutexHolder        0

/* Diverge rather than print: the same thing `src/main.rs`'s panic handler would
 * do before a console exists. A configASSERT that formatted would put a `printf`
 * in a size measurement. */
#define configASSERT( x )    if( ( x ) == 0 ) { for( ;; ) {} }

#endif /* FREERTOS_CONFIG_H */
