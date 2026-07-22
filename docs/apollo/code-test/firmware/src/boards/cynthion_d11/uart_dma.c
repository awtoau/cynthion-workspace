/**
 * DMA-backed UART RX driver for the Cynthion ATSAMD11.
 *
 * This file is part of Apollo.
 *
 * Copyright (c) 2024 Great Scott Gadgets <info@greatscottgadgets.com>
 * SPDX-License-Identifier: BSD-3-Clause
 */

/*
 * Design overview
 * ---------------
 * Two 128-byte half-buffers (ping and pong) are used as DMA destinations in
 * alternation.  The DMAC is configured with a base descriptor pointing at the
 * ping buffer and a write-back / linked descriptor pointing at the pong buffer.
 * When a transfer of 128 bytes completes the DMAC raises TCMPL on channel 0,
 * the ISR copies the completed half to the ring buffer and relinks the
 * descriptor chain so that the *other* half becomes the new active target.
 *
 * Ring buffer
 * -----------
 * 512 bytes, power-of-two size, accessed with a head/tail index pair.
 * head: next byte to write (producer = DMAC ISR)
 * tail: next byte to read  (consumer = console_task via uart_dma_read)
 * All index arithmetic is masked with (RING_SIZE - 1).
 *
 * DMAC channel
 * ------------
 * Channel 0, trigger source = SERCOM2_DMAC_ID_RX.
 * Beat size = 1 byte.  Trigger action = BEAT (one beat per trigger).
 * BLOCKACT = NOACT (don't suspend on block completion; TCMPL fires instead).
 *
 * Descriptor memory
 * -----------------
 * The SAMD11 DMAC requires all descriptors in a single SRAM section aligned
 * to 16 bytes.  We allocate:
 *   _dma_descriptors[0]  – base descriptor for channel 0 (ping target)
 *   _dma_wb[0]           – write-back descriptor for channel 0
 * A second, separately allocated linked descriptor (_desc_pong) provides the
 * pong transfer; its DESCADDR loops back to _dma_descriptors[0].
 */

#include <sam.h>
#include <stdint.h>
#include <string.h>

#include <hpl/pm/hpl_pm_base.h>
#include <hpl/gclk/hpl_gclk_base.h>

#include "uart_dma.h"

/* -------------------------------------------------------------------------
 * Constants
 * ---------------------------------------------------------------------- */

#define HALF_BUF_SIZE   128u   /* bytes per ping/pong half-buffer           */
#define RING_SIZE       512u   /* ring buffer size; must be a power of two  */
#define RING_MASK       (RING_SIZE - 1u)

/* SERCOM2 DMA RX trigger ID from the SAMD11 datasheet (table 19-8).
 * SERCOM0=0x04, SERCOM1=0x06, SERCOM2=0x08.                               */
#define SERCOM2_DMAC_ID_RX  0x08u

/* DMA channel we use for UART RX. */
#define DMA_CH  0u

/* -------------------------------------------------------------------------
 * DMA descriptor memory
 *
 * The SAMD11/21 DMAC needs two aligned SRAM regions:
 *   - Base descriptor table   (one entry per channel, indexed by channel)
 *   - Write-back descriptor   (one entry per channel, updated by hardware)
 *
 * Both must be 16-byte aligned.
 * ---------------------------------------------------------------------- */
typedef struct {
    uint16_t BTCTRL;   /* Beat transfer control                            */
    uint16_t BTCNT;    /* Beat count                                        */
    uint32_t SRCADDR;  /* Source address                                    */
    uint32_t DSTADDR;  /* Destination address                               */
    uint32_t DESCADDR; /* Next descriptor address (0 = end of chain)        */
} dma_desc_t;

/* Base descriptor table and write-back area placed in a dedicated section
 * so the linker script can position them in SRAM if needed.  The
 * __attribute__((aligned(16))) ensures the DMAC's alignment requirement.  */
static volatile dma_desc_t _dma_descriptors[1] __attribute__((aligned(16)));
static volatile dma_desc_t _dma_wb[1]           __attribute__((aligned(16)));

/* Pong descriptor is a second standalone descriptor in ordinary SRAM.      */
static dma_desc_t _desc_pong __attribute__((aligned(16)));

/* -------------------------------------------------------------------------
 * Ping-pong half-buffers and ring buffer
 * ---------------------------------------------------------------------- */
static uint8_t _ping_buf[HALF_BUF_SIZE];
static uint8_t _pong_buf[HALF_BUF_SIZE];

static uint8_t  _ring[RING_SIZE];
static volatile uint32_t _ring_head = 0;  /* write index (ISR)              */
static volatile uint32_t _ring_tail = 0;  /* read  index (console_task)     */

/* Which half-buffer is currently being filled by the DMA (0=ping, 1=pong). */
static volatile uint8_t _active_half = 0;

/* -------------------------------------------------------------------------
 * Helpers
 * ---------------------------------------------------------------------- */

/**
 * Fill a DMA descriptor for a SERCOM2 RX → buffer transfer.
 *
 * @param desc     Pointer to the descriptor to fill.
 * @param dst      Destination buffer (byte array).
 * @param count    Number of bytes to transfer.
 * @param next     DESCADDR to write (address of the next descriptor, or 0).
 */
static void _fill_desc(dma_desc_t *desc, uint8_t *dst, uint16_t count, uint32_t next)
{
    /*
     * BTCTRL bits used:
     *   VALID    = 1  – descriptor is valid
     *   EVOSEL   = 0  – no event output
     *   BLOCKACT = 0  – NOACT (do not suspend channel after block; TCMPL fires)
     *   BEATSIZE = 0  – 1-byte beats
     *   SRCINC   = 0  – source (SERCOM DATA) address is fixed
     *   DSTINC   = 1  – destination (SRAM buffer) increments per beat
     *   STEPSEL  = 0  – step size applies to destination
     *   STEPSIZE = 0  – x1 step
     */
    desc->BTCTRL   = DMAC_BTCTRL_VALID
                   | DMAC_BTCTRL_BEATSIZE_BYTE
                   | DMAC_BTCTRL_DSTINC;
    desc->BTCNT    = count;
    desc->SRCADDR  = (uint32_t)&SERCOM2->USART.DATA.reg;
    /* For incrementing destination, SRCADDR must point one past the end.   */
    desc->DSTADDR  = (uint32_t)(dst + count);
    desc->DESCADDR = next;
}

/**
 * Copy a completed half-buffer into the ring buffer.
 *
 * Called from the DMAC ISR.  The ring is a power-of-two so we never have to
 * worry about wrap-around of the index itself; we only mask on access.
 *
 * If the ring is full, bytes are dropped (head advances over tail).
 * Overflow is a programming error at the chosen buffer sizes; flag it with
 * a counter for debugging.
 */
static volatile uint32_t _ring_overflow_count = 0;

static void _copy_half_to_ring(const uint8_t *src, uint32_t count)
{
    for (uint32_t i = 0; i < count; i++) {
        uint32_t next_head = (_ring_head + 1u) & RING_MASK;
        if (next_head == _ring_tail) {
            /* Ring full — drop this byte and all remaining in this burst.  */
            _ring_overflow_count++;
            continue;
        }
        _ring[_ring_head] = src[i];
        _ring_head = next_head;
    }
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

/**
 * Initialise the DMAC and begin ping-pong DMA on SERCOM2 RX.
 */
void uart_dma_init(void)
{
    /* Disable the SERCOM2 RXC interrupt — DMA owns the receive path now.
     * We leave SERCOM2_IRQn enabled for the error-flag handler in uart.c.  */
    SERCOM2->USART.INTENCLR.reg = SERCOM_USART_INTENCLR_RXC;

    /* Enable the DMAC APB clock (PM_BUS_AHB for DMAC on SAMD11).          */
    PM->AHBMASK.bit.DMAC_ = 1;
    PM->APBBMASK.bit.DMAC_ = 1;

    /* Disable DMAC while configuring.                                       */
    DMAC->CTRL.bit.DMAENABLE = 0;
    DMAC->CTRL.bit.SWRST = 1;
    while (DMAC->CTRL.bit.SWRST);

    /* Point the DMAC at our descriptor and write-back arrays.               */
    DMAC->BASEADDR.reg = (uint32_t)_dma_descriptors;
    DMAC->WRBADDR.reg  = (uint32_t)_dma_wb;

    /* Enable priority levels 0–3.                                           */
    DMAC->CTRL.reg = DMAC_CTRL_DMAENABLE
                   | DMAC_CTRL_LVLEN(0xFu);

    /* --- Build the initial ping descriptor (channel 0 base). ---           */
    _fill_desc((dma_desc_t *)&_dma_descriptors[DMA_CH],
               _ping_buf, HALF_BUF_SIZE,
               (uint32_t)&_desc_pong);

    /* --- Build the pong descriptor; links back to ping. ---                */
    _fill_desc(&_desc_pong,
               _pong_buf, HALF_BUF_SIZE,
               (uint32_t)&_dma_descriptors[DMA_CH]);

    _active_half = 0; /* DMA will fill ping first.                           */

    /* --- Configure DMAC channel 0. ---
     *
     * CHCTRLB:
     *   TRIGACT = BEAT      – one beat (1 byte) per trigger
     *   TRIGSRC = SERCOM2RX – trigger from SERCOM2 RX data-ready
     *   LVL     = 0         – lowest priority level
     *   EVOE/EVIE = 0       – no event in/out
     */
    DMAC->CHID.reg = DMAC_CHID_ID(DMA_CH);
    DMAC->CHCTRLB.reg = DMAC_CHCTRLB_TRIGACT_BEAT
                      | DMAC_CHCTRLB_TRIGSRC(SERCOM2_DMAC_ID_RX)
                      | DMAC_CHCTRLB_LVL(0);

    /* Enable transfer-complete interrupt on channel 0.                      */
    DMAC->CHINTENSET.reg = DMAC_CHINTENSET_TCMPL;

    /* Enable the channel.                                                   */
    DMAC->CHCTRLA.bit.ENABLE = 1;

    /* Enable the DMAC IRQ in the NVIC.                                      */
    NVIC_EnableIRQ(DMAC_IRQn);
}


/**
 * Returns the number of bytes available in the ring buffer.
 */
uint32_t uart_dma_available(void)
{
    /* Read head and tail once each to avoid tearing.                        */
    uint32_t head = _ring_head;
    uint32_t tail = _ring_tail;
    return (head - tail) & RING_MASK;
}


/**
 * Drain up to @p len bytes from the ring buffer into @p buf.
 */
uint32_t uart_dma_read(uint8_t *buf, uint32_t len)
{
    uint32_t avail = uart_dma_available();
    if (len > avail) {
        len = avail;
    }
    for (uint32_t i = 0; i < len; i++) {
        buf[i] = _ring[_ring_tail];
        _ring_tail = (_ring_tail + 1u) & RING_MASK;
    }
    return len;
}

/* -------------------------------------------------------------------------
 * DMAC ISR
 * ---------------------------------------------------------------------- */

/**
 * DMAC interrupt handler.
 *
 * Fires when a half-buffer DMA transfer (HALF_BUF_SIZE bytes) completes.
 * Copies the completed half into the ring buffer, then alternates the active
 * half so the DMAC continues into the other buffer.
 *
 * The descriptor chain is already circular (ping → pong → ping …) so the
 * DMAC automatically restarts the transfer into the *other* half as soon as
 * TCMPL is raised.  We only need to copy the data that just completed.
 */
void DMAC_Handler(void)
{
    /* Select channel 0 to read its interrupt flags.                         */
    DMAC->CHID.reg = DMAC_CHID_ID(DMA_CH);

    if (DMAC->CHINTFLAG.bit.TCMPL) {
        /* Clear the interrupt flag.                                         */
        DMAC->CHINTFLAG.reg = DMAC_CHINTFLAG_TCMPL;

        /* Copy the half that just completed.  _active_half tells us which
         * descriptor was running when TCMPL fired.  Because the DMAC has
         * already advanced to the next descriptor (pong or ping), the
         * completed data is in the *previous* half.                         */
        if (_active_half == 0) {
            _copy_half_to_ring(_ping_buf, HALF_BUF_SIZE);
            _active_half = 1;
        } else {
            _copy_half_to_ring(_pong_buf, HALF_BUF_SIZE);
            _active_half = 0;
        }
    }

    /* Clear any channel error flag (defensive; should not occur).           */
    if (DMAC->CHINTFLAG.bit.TERR) {
        DMAC->CHINTFLAG.reg = DMAC_CHINTFLAG_TERR;
    }
}
