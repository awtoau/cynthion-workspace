/*
 * USB bulk throughput, measured with no interpreted language in the path.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Every host-side measurement so far has been Python, and although the last of
 * them called libusb directly with no per-byte work, that is still a Python
 * process holding the handle and running the event loop. This removes the
 * question entirely rather than reasoning about it: a C program calling libusb
 * with no runtime, no interpreter and no allocation inside the timed region.
 *
 * If this reports the same rate the Python versions did -- around 290 Mbps --
 * then the host language was never the limit, and the remaining gap to the
 * ~426 Mbps bulk ceiling is device-side or topology. If it reports
 * substantially more, the Python measurements were understating the link.
 *
 * Build:
 *     gcc -O2 -o tmp/usb_speed_native scripts/usb_speed_native.c -lusb-1.0
 *
 * Run:
 *     ./tmp/usb_speed_native [seconds] [queue_depth]
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <libusb-1.0/libusb.h>

#define VENDOR_ID   0x1d50
#define PRODUCT_ID  0x615b

#define EP_IN       0x81
#define EP_OUT      0x01

/* 64 KiB per transfer, matching the Python scripts so the comparison isolates
 * the language rather than confounding it with a size change. */
#define CHUNK       (64 * 1024)

/* Transfers kept in flight. Depth 1 reproduces the synchronous case. */
#define MAX_DEPTH   32

static volatile int running = 1;
static volatile unsigned long long total_bytes = 0;

static double now_seconds(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* Resubmitted from inside the callback rather than from the main loop: waiting
 * would reintroduce the idle gap between transfers that queuing exists to
 * remove. */
static void LIBUSB_CALL on_complete(struct libusb_transfer *transfer)
{
    if (transfer->status != LIBUSB_TRANSFER_COMPLETED) {
        running = 0;
        return;
    }

    total_bytes += transfer->actual_length;

    if (running && libusb_submit_transfer(transfer) != 0) {
        running = 0;
    }
}

static double measure(libusb_device_handle *handle, unsigned char endpoint,
                      double seconds, int depth, unsigned char *buffer)
{
    struct libusb_transfer *transfers[MAX_DEPTH];
    int i;

    total_bytes = 0;
    running = 1;

    for (i = 0; i < depth; i++) {
        transfers[i] = libusb_alloc_transfer(0);
        libusb_fill_bulk_transfer(transfers[i], handle, endpoint,
                                  buffer, CHUNK, on_complete, NULL, 2000);
    }

    double start = now_seconds();
    for (i = 0; i < depth; i++) {
        if (libusb_submit_transfer(transfers[i]) != 0) {
            running = 0;
        }
    }

    struct timeval timeout = { 0, 100000 };
    while (running && (now_seconds() - start) < seconds) {
        libusb_handle_events_timeout(NULL, &timeout);
    }

    double elapsed = now_seconds() - start;
    running = 0;

    /* Cancel and drain, so the byte count reflects what actually crossed the
     * bus rather than what was queued. */
    for (i = 0; i < depth; i++) {
        libusb_cancel_transfer(transfers[i]);
    }
    double drain_until = now_seconds() + 0.5;
    while (now_seconds() < drain_until) {
        libusb_handle_events_timeout(NULL, &timeout);
    }
    for (i = 0; i < depth; i++) {
        libusb_free_transfer(transfers[i]);
    }

    return elapsed;
}

int main(int argc, char **argv)
{
    double seconds = (argc > 1) ? atof(argv[1]) : 3.0;
    int depth = (argc > 2) ? atoi(argv[2]) : 8;

    if (depth < 1 || depth > MAX_DEPTH) {
        fprintf(stderr, "depth must be 1..%d\n", MAX_DEPTH);
        return 1;
    }

    if (libusb_init(NULL) != 0) {
        fprintf(stderr, "libusb_init failed\n");
        return 1;
    }

    libusb_device_handle *handle =
        libusb_open_device_with_vid_pid(NULL, VENDOR_ID, PRODUCT_ID);
    if (!handle) {
        fprintf(stderr, "device %04x:%04x not found -- is the one-way "
                        "bitstream loaded and AUX cabled?\n",
                VENDOR_ID, PRODUCT_ID);
        libusb_exit(NULL);
        return 1;
    }

    libusb_set_auto_detach_kernel_driver(handle, 1);
    if (libusb_claim_interface(handle, 0) != 0) {
        fprintf(stderr, "could not claim interface 0\n");
        libusb_close(handle);
        libusb_exit(NULL);
        return 1;
    }

    /* Allocated once, outside the timed region. Filled with a counting
     * sequence so the OUT direction sends the same pattern the Python scripts
     * did; nothing reads it back here, since correctness is checked in
     * gateware and by the Python tools. */
    unsigned char *buffer = malloc(CHUNK);
    if (!buffer) {
        fprintf(stderr, "allocation failed\n");
        return 1;
    }
    for (int i = 0; i < CHUNK; i++) {
        buffer[i] = (unsigned char)(i & 0xFF);
    }

    printf("native libusb, %d KiB transfers, depth %d\n", CHUNK / 1024, depth);
    printf("  %-6s %10s %10s %10s\n", "dir", "MiB", "MB/s", "Mbps");

    struct { const char *name; unsigned char endpoint; } directions[] = {
        { "IN",  EP_IN  },
        { "OUT", EP_OUT },
    };

    for (int d = 0; d < 2; d++) {
        double elapsed = measure(handle, directions[d].endpoint,
                                 seconds, depth, buffer);
        if (elapsed <= 0 || total_bytes == 0) {
            printf("  %-6s no data\n", directions[d].name);
            continue;
        }
        double rate = total_bytes / elapsed;
        printf("  %-6s %10.1f %10.2f %10.1f\n",
               directions[d].name,
               total_bytes / (double)(1 << 20),
               rate / 1e6,
               rate * 8 / 1e6);
    }

    free(buffer);
    libusb_release_interface(handle, 0);
    libusb_close(handle);
    libusb_exit(NULL);
    return 0;
}
