/**
 * Console handling code.
 *
 * This file is part of Apollo.
 *
 * Copyright (c) 2019-2024 Great Scott Gadgets <info@greatscottgadgets.com>
 * SPDX-License-Identifier: BSD-3-Clause
 */

#include <tusb.h>

#include "led.h"
#include "uart.h"

// On the Cynthion D11 board the DMA-backed RX path is available.
// Include its header only when the board supplies it.
#if __has_include("uart_dma.h")
#include "uart_dma.h"
#define CONSOLE_USE_UART_DMA 1
#else
#define CONSOLE_USE_UART_DMA 0
#endif


extern bool uart_active;

// Count of UART→host bytes dropped because the TinyUSB TX buffer was full.
static uint32_t uart_rx_drop_count = 0;


/**
 * Pass any data received via UART directly up to the host.
 *
 * Used only on boards that do NOT have DMA RX (CONSOLE_USE_UART_DMA == 0).
 * Does NOT flush — bytes accumulate in the TinyUSB TX buffer and are flushed
 * as a batch at the end of console_task().  Flushing after every byte forces
 * one USB packet per byte, wasting bandwidth and stressing the USB stack.
 *
 * The weak symbol in uart.c remains the default; this definition overrides it
 * when console.c is linked on non-DMA boards.
 */
void uart_byte_received_cb(uint8_t byte)
{
	if (tud_cdc_write_char(byte) == 0) {
		// TinyUSB TX buffer is full; the byte is lost.
		uart_rx_drop_count++;
	}
}


/**
 * Main task that handles console I/O.
 */
void console_task(void)
{
	if (!tud_cdc_connected()) {
		return;
	}

	// We can send data to the FPGA over UART iff:
	//  - there's data waiting for us to send, and
	//  - the UART has room in its FIFO
	//
	// If both conditions are met, send data.
	while (uart_ready_for_write() && tud_cdc_available()) {
		uint8_t byte = tud_cdc_read_char();
		uart_nonblocking_write(byte);
	}

#if CONSOLE_USE_UART_DMA
	// DMA path: drain the ring buffer into the TinyUSB TX buffer.
	// uart_dma_available() / uart_dma_read() are safe to call from the main
	// task; the ring head is only advanced by the DMAC ISR.
	{
		uint8_t dma_chunk[64];
		uint32_t avail;
		while ((avail = uart_dma_available()) > 0) {
			uint32_t n = uart_dma_read(dma_chunk,
			                           avail < sizeof(dma_chunk) ? avail : sizeof(dma_chunk));
			for (uint32_t i = 0; i < n; i++) {
				if (tud_cdc_write_char(dma_chunk[i]) == 0) {
					uart_rx_drop_count++;
				}
			}
		}
	}
#endif

	// Flush any UART→host bytes that accumulated in the TinyUSB TX buffer
	// during this tick.  One flush here sends them as one USB packet rather
	// than one per byte.
	tud_cdc_write_flush();
}

//
// We defer initializing our UART until we get a CDC connection.
//
// This prevents contention if the FPGA lines are used for something else,
// but makes everything seem to Just Work (TM) once the user starts using
// the CDC-ACM connection.
//


/**
 * Call-back issued when the host's line-coding changes.
 */
void tud_cdc_line_coding_cb(uint8_t itf, cdc_line_coding_t const* coding)
{
	uart_initialize(true, coding->bit_rate);
}


/**
 * Other callbacks: if our UART isn't active, initialize it.
 */

void tud_cdc_rx_wanted_cb(uint8_t itf, char wanted_char)
{
	if (!uart_active) {
		uart_initialize(true, 115200);
	}
}

void tud_cdc_line_state_cb(uint8_t itf, bool dtr, bool rts)
{
	if (!uart_active) {
		uart_initialize(true, 115200);
	}
}
