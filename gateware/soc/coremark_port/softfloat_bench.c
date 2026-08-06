/* Soft-float cost on a core with no FPU.
 *
 * Neither VexRiscv nor the VexiiRiscv configurations built here have hardware
 * floating point, so every float operation is a libgcc call. This measures how
 * much that costs, separately for single and double precision, against the
 * integer multiply the ECP5's DSP blocks do in hardware.
 *
 * Single precision is the interesting case: embedded code that needs floats
 * usually needs `float`, and `double` doubles the work for range that is
 * rarely used.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

#include "console.h"

#define N 20000

static unsigned long long cycles(void)
{
    unsigned int hi, lo, hi2;
    do {
        __asm__ volatile ("csrr %0, mcycleh" : "=r"(hi));
        __asm__ volatile ("csrr %0, mcycle"  : "=r"(lo));
        __asm__ volatile ("csrr %0, mcycleh" : "=r"(hi2));
    } while (hi != hi2);
    return ((unsigned long long)hi << 32) | lo;
}

static void report(const char *label, unsigned long long span)
{
    print(label);
    print(" ");
    print_hex((unsigned int)(span / N));
    print(" cycles/op (");
    /* Decimal, since hex cycle counts are hard to compare by eye. */
    unsigned int per = (unsigned int)(span / N);
    char buf[12]; int n = 0;
    if (!per) buf[n++] = '0';
    while (per) { buf[n++] = (char)('0' + per % 10); per /= 10; }
    while (n--) putch(buf[n]);
    print(")\r\n");
}

int main(void)
{
    print("\r\nSoft-float cost, no FPU present\r\n\r\n");

    /* volatile so the compiler cannot fold the loop away or hoist the
     * arithmetic out of it -- an optimised-away loop reports zero cycles and
     * looks like a spectacular result. */
    volatile int   ia = 1103515245, ib = 12345;
    volatile float fa = 1.0001f,    fb = 3.14159f;
    volatile double da = 1.0001,    db = 3.14159;

    unsigned long long t0, t1;

    t0 = cycles();
    for (int i = 0; i < N; i++) { volatile int r = ia * ib; (void)r; }
    t1 = cycles();
    report("int32   mul :", t1 - t0);

    t0 = cycles();
    for (int i = 0; i < N; i++) { volatile float r = fa * fb; (void)r; }
    t1 = cycles();
    report("float32 mul :", t1 - t0);

    t0 = cycles();
    for (int i = 0; i < N; i++) { volatile float r = fa + fb; (void)r; }
    t1 = cycles();
    report("float32 add :", t1 - t0);

    t0 = cycles();
    for (int i = 0; i < N; i++) { volatile float r = fa / fb; (void)r; }
    t1 = cycles();
    report("float32 div :", t1 - t0);

    t0 = cycles();
    for (int i = 0; i < N; i++) { volatile double r = da * db; (void)r; }
    t1 = cycles();
    report("float64 mul :", t1 - t0);

    t0 = cycles();
    for (int i = 0; i < N; i++) { volatile double r = da + db; (void)r; }
    t1 = cycles();
    report("float64 add :", t1 - t0);

    t0 = cycles();
    for (int i = 0; i < N; i++) { volatile double r = da / db; (void)r; }
    t1 = cycles();
    report("float64 div :", t1 - t0);

    print("\r\nSOFTFLOAT-DONE\r\n");
    for (;;) { }
    return 0;
}
