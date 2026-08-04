/* The per-task RAM cost, as symbol sizes the compiler computes rather than
 * numbers read off a header by eye. `scripts/soc_freertos_probe.py` compiles
 * this on its own -- no LTO, no link, no `--gc-sections` -- and reports what
 * `nm -S` says each array is.
 *
 * A TCB and a stack per task is the whole of what separates this model from
 * every other one in `docs/soc-concurrency-models.md`, so it is worth measuring
 * rather than quoting.
 */
#include "FreeRTOS.h"
#include "semphr.h"
#include "task.h"

char probe_static_task[ sizeof( StaticTask_t ) ];
char probe_static_queue[ sizeof( StaticSemaphore_t ) ];
char probe_minimal_stack[ configMINIMAL_STACK_SIZE * sizeof( StackType_t ) ];
char probe_stack_type[ sizeof( StackType_t ) ];
