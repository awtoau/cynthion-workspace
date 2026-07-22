/**
 * DMA-backed UART RX driver for the Cynthion ATSAMD11.
 *
 * Implements ping-pong DMA on SERCOM2 RX using DMAC channel 0.
 * Received bytes are deposited into a 512-byte power-of-two ring buffer
 * that the main task drains via uart_dma_read().
 *
 * This file is part of Apollo.
 *
 * Copyright (c) 2024 Great Scott Gadgets <info@greatscottgadgets.com>
 * SPDX-License-Identifier: BSD-3-Clause
 */

#ifndef __UART_DMA_H__
#define __UART_DMA_H__

#include <stdint.h>
#include <stddef.h>

/**
 * Initialise the DMAC and begin ping-pong DMA on SERCOM2 RX.
 *
 * Call this instead of (or after) the NVIC_EnableIRQ(SERCOM2_IRQn) in
 * uart_initialize().  The SERCOM2 RXC interrupt enable in INTENSET is
 * cleared here; all byte reception is handled by the DMA transfer-complete
 * interrupt.
 *
 * The SERCOM2_Handler is kept active for error-flag handling (BUFOVF/FERR/PERR)
 * but its RXC branch is bypassed because RXC will not fire while DMA owns the
 * receive path.
 */
void uart_dma_init(void);

/**
 * Returns the number of bytes available to read from the DMA ring buffer.
 */
uint32_t uart_dma_available(void);

/**
 * Drain up to @p len bytes from the DMA ring buffer into @p buf.
 *
 * @param buf  Destination buffer; must be at least @p len bytes.
 * @param len  Maximum number of bytes to read.
 * @return     Number of bytes actually copied (0 … len).
 */
uint32_t uart_dma_read(uint8_t *buf, uint32_t len);

#endif /* __UART_DMA_H__ */
