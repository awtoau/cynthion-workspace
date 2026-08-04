/* What FreeRTOS's `#include <stdlib.h>` actually needs, for a freestanding
 * build with no rv32 libc. `tasks.c`, `list.c` and `queue.c` include it for
 * `size_t` and `NULL` and, with configSUPPORT_DYNAMIC_ALLOCATION off, call
 * nothing from it.
 *
 * A shim rather than a libc because there is no rv32 newlib beside this cross
 * compiler, and because pulling one in would put its code in a measurement of
 * the kernel.
 */
#ifndef FREERTOS_MODEL_STDLIB_H
#define FREERTOS_MODEL_STDLIB_H

#include <stddef.h>

#endif
