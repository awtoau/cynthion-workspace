/* CoreMark port for the Cynthion block-RAM SoC.
 *
 * Two hooks the benchmark needs from a platform: a monotonic tick source and a
 * way to print. Ticks come from the RISC-V `mcycle` counter, so a run measures
 * CPU cycles directly and CoreMark/MHz falls out without knowing the clock.
 * Printing goes to the console peripheral, the same one the hello firmware
 * uses.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include "coremark.h"
#include <stdarg.h>

#if VALIDATION_RUN
volatile ee_s32 seed1_volatile = 0x3415;
volatile ee_s32 seed2_volatile = 0x3415;
volatile ee_s32 seed3_volatile = 0x66;
#endif
#if PERFORMANCE_RUN
volatile ee_s32 seed1_volatile = 0x0;
volatile ee_s32 seed2_volatile = 0x0;
volatile ee_s32 seed3_volatile = 0x66;
#endif
#if PROFILE_RUN
volatile ee_s32 seed1_volatile = 0x8;
volatile ee_s32 seed2_volatile = 0x8;
volatile ee_s32 seed3_volatile = 0x8;
#endif
volatile ee_s32 seed4_volatile = ITERATIONS;
volatile ee_s32 seed5_volatile = 0;

/* Console: the NS16550A in ecp5-test/riscv/uart16550.py. Bit 31 is set because
 * the data cache treats an access as uncached I/O only when it is -- a
 * peripheral below 0x80000000 has its stores absorbed by the cache.
 *
 * THR at +0, LSR at +5. They are four bytes apart on purpose: LSR is what this
 * loop polls, and it must not share a 32-bit word with a register whose read
 * has a side effect. */
#define CONSOLE_BASE  0xf0000000u
#define CONSOLE_THR   (*(volatile unsigned char *)(CONSOLE_BASE + 0))
#define CONSOLE_LSR   (*(volatile unsigned char *)(CONSOLE_BASE + 5))
#define CONSOLE_THRE  0x20u

static void putch(char c)
{
    while (!(CONSOLE_LSR & CONSOLE_THRE)) { }
    CONSOLE_THR = (unsigned char)c;
}

/* mcycle is 64-bit, read as two 32-bit halves. The retry guards the case where
 * the low half wraps between the two reads, which would otherwise report a
 * time roughly 2^32 cycles wrong -- about 70 seconds at 60 MHz, and silently
 * plausible. */
static unsigned long long read_cycles(void)
{
    unsigned int hi, lo, hi2;
    do {
        __asm__ volatile ("csrr %0, mcycleh" : "=r"(hi));
        __asm__ volatile ("csrr %0, mcycle"  : "=r"(lo));
        __asm__ volatile ("csrr %0, mcycleh" : "=r"(hi2));
    } while (hi != hi2);
    return ((unsigned long long)hi << 32) | lo;
}

static CORE_TICKS start_time_val, stop_time_val;

void start_time(void) { start_time_val = (CORE_TICKS)read_cycles(); }
void stop_time(void)  { stop_time_val  = (CORE_TICKS)read_cycles(); }

CORE_TICKS get_time(void) { return stop_time_val - start_time_val; }

/* HAS_FLOAT is 0, so the harness reports ticks and never asks for seconds.
 * Ticks are CPU cycles, which is the better figure anyway: CoreMark/MHz is
 * iterations divided by cycles, with no need to know the clock exactly. There
 * is no FPU here, and double arithmetic would pull in soft-float helpers that
 * -nostdlib excludes. */
secs_ret time_in_secs(CORE_TICKS ticks)
{
    return (secs_ret)ticks;
}

ee_u32 default_num_contexts = 1;

void portable_init(core_portable *p, int *argc, char *argv[])
{
    (void)argc; (void)argv;
    if (sizeof(ee_ptr_int) != sizeof(ee_u8 *))
        ee_printf("ERROR! ee_ptr_int is not the same size as a pointer!\n");
    if (sizeof(ee_u32) != 4)
        ee_printf("ERROR! ee_u32 is not 32 bits!\n");
    p->portable_id = 1;
}

void portable_fini(core_portable *p)
{
    p->portable_id = 0;
    /* The harness reads until it sees this, so it must be the last line. */
    ee_printf("\nCOREMARK-DONE\n");
}

/* A minimal printf: CoreMark uses %d, %u, %s, %c, %f and %x only. Pulling in a
 * real libc would mean a heap and file descriptors on a machine with neither. */
static void print_padded(unsigned long v, unsigned base, int width, int zero)
{
    char buf[24];
    int n = 0;
    if (v == 0) buf[n++] = '0';
    while (v && n < (int)sizeof(buf)) {
        unsigned d = (unsigned)(v % base);
        buf[n++] = (char)(d < 10 ? '0' + d : 'a' + d - 10);
        v /= base;
    }
    for (int i = n; i < width; i++) putch(zero ? '0' : ' ');
    while (n--) putch(buf[n]);
}

static void print_unsigned(unsigned long v, unsigned base)
{
    char buf[24];
    int n = 0;
    if (v == 0) { putch('0'); return; }
    while (v && n < (int)sizeof(buf)) {
        unsigned d = (unsigned)(v % base);
        buf[n++] = (char)(d < 10 ? '0' + d : 'a' + d - 10);
        v /= base;
    }
    while (n--) putch(buf[n]);
}

int ee_printf(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    for (const char *p = fmt; *p; p++) {
        if (*p != '%') { putch(*p); continue; }

        p++;
        /* Skip flags, width and precision -- CoreMark uses %04x and %.2f, and
         * the values matter more than the padding. Not parsing them prints the
         * format strings themselves, which looks like a benchmark that
         * produced no numbers. */
        int zero_pad = 0, width = 0;
        while (*p == '-' || *p == '+' || *p == ' ' || *p == '#' || *p == '0') {
            if (*p == '0') zero_pad = 1;
            p++;
        }
        while (*p >= '0' && *p <= '9') { width = width * 10 + (*p - '0'); p++; }
        if (*p == '.') { p++; while (*p >= '0' && *p <= '9') p++; }
        /* Length modifiers: l, ll, h, hh, z. Everything here is 32-bit, so
         * they change nothing except how many characters to skip. */
        while (*p == 'l' || *p == 'h' || *p == 'z') p++;

        switch (*p) {
        case 'd': case 'i': {
            long v = va_arg(ap, int);
            if (v < 0) { putch('-'); v = -v; }
            print_padded((unsigned long)v, 10, width, zero_pad);
            break;
        }
        case 'u': print_padded(va_arg(ap, unsigned int), 10, width, zero_pad);
                  break;
        case 'x': case 'X':
                  print_padded(va_arg(ap, unsigned int), 16, width, zero_pad);
                  break;
        case 'c': putch((char)va_arg(ap, int)); break;
        case 's': {
            const char *s = va_arg(ap, const char *);
            while (s && *s) putch(*s++);
            break;
        }
        case 'f': case 'F': case 'g': case 'G': case 'e': case 'E': {
            /* Promoted to double by the call regardless of the source type.
             * Printed to three decimals by hand: there is no FPU, and pulling
             * in a formatting libc for one figure is not worth it. */
            double d = va_arg(ap, double);
            if (d < 0) { putch('-'); d = -d; }
            unsigned long whole = (unsigned long)d;
            print_padded(whole, 10, 0, 0);
            putch('.');
            unsigned long frac = (unsigned long)((d - (double)whole) * 1000.0);
            if (frac < 100) putch('0');
            if (frac < 10)  putch('0');
            print_padded(frac, 10, 0, 0);
            break;
        }
        case '%': putch('%'); break;
        default:  putch('%'); putch(*p); break;
        }
    }
    va_end(ap);
    return 0;
}
