/* `port.c` includes this for `memset`, which it uses to fill a new task's
 * stack. The definition is in `main.c`; this only declares it. See
 * `shim/stdlib.h` for why there is no libc here.
 */
#ifndef FREERTOS_MODEL_STRING_H
#define FREERTOS_MODEL_STRING_H

#include <stddef.h>

void * memset( void * s, int c, size_t n );
void * memcpy( void * d, const void * s, size_t n );
char * strncpy( char * d, const char * s, size_t n );

#endif
