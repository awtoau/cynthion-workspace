/*
 * ARCHIVED -- not built. Retired 2026-07-29.
 *
 * DMAC-driven spi_send() for the cynthion_d11 board. This works: it was built,
 * flashed, and verified on hardware (ECP5 status DONE=1, no FAIL/BSE error bits,
 * 5 consecutive configures). It is archived rather than shipped because it is
 * NOT faster, and it is kept because the working DMAC setup -- trigger sources,
 * the end-address rule, channel priority and ordering -- is fiddly enough to be
 * worth not rediscovering.
 *
 * Measured on Cynthion r1.4, 304726-byte bitstream:
 *
 *   polled (pipelined) baseline   1698-1715 ms   marginal clocking 0.770 us/byte
 *   DMA                           1711-1751 ms   marginal clocking 0.880 us/byte
 *
 *   cost of DMA: +348 B flash (11232 -> 11580, 78.35% -> 80.78% of 14 KB)
 *                 +96 B RAM   (2728 -> 2824)
 *
 * Why there was nothing to win: the premise for this work was that the SERCOM
 * loop cost 3.93 us/byte against a 0.667 us/byte wire. That figure came from
 * dividing a whole 1006 us SCAN call by its 256 bytes, which charges the fixed
 * ~200 us USB round trip and per-call overhead to per-byte clocking. Measuring
 * the marginal cost instead -- differencing scans of 64 and 2048 bits over the
 * same round trip, see scripts/probe_scan_effect.py -- shows the polled loop was
 * already running at 0.770 us/byte, i.e. essentially at the wire floor. The
 * pipelined loop had already taken the win; there was no CPU bottleneck left for
 * DMA to remove, so DMA only added setup cost per 256-byte chunk.
 *
 * If the chunk size ever grows a lot, or SCK is raised well above 12 MHz, the
 * fixed setup cost would amortise better and this becomes worth re-testing.
 *
 * ---------------------------------------------------------------------------
 *
 * SPI driver code.
 *
 * This file is part of Apollo.
 *
 * Copyright (c) 2020-2024 Great Scott Gadgets <info@greatscottgadgets.com>
 * SPDX-License-Identifier: BSD-3-Clause
 */

#include <sam.h>

#include <hpl/pm/hpl_pm_base.h>
#include <hpl/gclk/hpl_gclk_base.h>
#include <hal/include/hal_gpio.h>

#include <string.h>

#include "spi.h"
#include "led.h"


// Hide the ugly Atmel Sercom object name.
typedef Sercom sercom_t;


/**
 * DMA support for the FPGA JTAG SERCOM.
 *
 * The byte-at-a-time SERCOM loop below costs ~3.93 us/byte against a wire that
 * only needs 0.67 us/byte at BAUD=1 (12 MHz SCK on a 48 MHz core) -- the CPU,
 * not the link, sets the pace. Handing the transfer to the DMAC lets the
 * peripheral clock bytes back to back with no per-byte CPU involvement.
 *
 * Two channels are needed because SPI is full duplex: every byte clocked out
 * clocks one in, and if nothing drains DATA the RX path overruns and the
 * transfer stalls. So TX is fed from the caller's buffer and RX is drained into
 * it, both by DMA.
 *
 * The RX channel is given the higher priority (lower channel number wins at
 * equal priority level on this part) so a received byte is always collected
 * before the next TX beat is serviced.
 */
#define SPI_DMA_CHANNEL_RX  0
#define SPI_DMA_CHANNEL_TX  1

// Transfers shorter than this stay on the polled path: DMA setup is a fixed
// cost of a few dozen register writes, which is not worth paying for a handful
// of bytes.
#define SPI_DMA_MIN_LENGTH  8

// Descriptor and write-back sections. The DMAC requires both to be 128-bit
// aligned, and indexes them by channel number, so each must have one slot per
// channel we use -- 2 channels * 16 bytes * 2 sections = 64 bytes of RAM.
static DmacDescriptor spi_dma_descriptors[2] __attribute__((aligned(16)));
static DmacDescriptor spi_dma_writeback[2]   __attribute__((aligned(16)));

// A sink for transfers whose response the caller does not want. Keeping the
// destination address fixed (DSTINC clear) means a single byte suffices.
static uint8_t spi_dma_scratch;

static bool spi_dma_ready = false;


/**
 * Brings up the DMAC for SERCOM SPI transfers. Idempotent.
 */
static void spi_dma_init(void)
{
	if (spi_dma_ready) {
		return;
	}

	// The DMAC needs its bus clocks running before any register will stick.
	// Unlike the SERCOMs it lives on the AHB as well as the APB.
	PM->AHBMASK.reg  |= PM_AHBMASK_DMAC;
	PM->APBBMASK.reg |= PM_APBBMASK_DMAC;

	// Reset to a known state, since we may be re-initialising.
	DMAC->CTRL.reg = 0;
	DMAC->CTRL.reg = DMAC_CTRL_SWRST;
	while (DMAC->CTRL.bit.SWRST);

	memset(spi_dma_descriptors, 0, sizeof(spi_dma_descriptors));
	memset(spi_dma_writeback, 0, sizeof(spi_dma_writeback));

	DMAC->BASEADDR.reg = (uint32_t)spi_dma_descriptors;
	DMAC->WRBADDR.reg  = (uint32_t)spi_dma_writeback;

	// Enable the DMAC and all four priority levels.
	DMAC->CTRL.reg = DMAC_CTRL_DMAENABLE | DMAC_CTRL_LVLEN0 | DMAC_CTRL_LVLEN1 |
			 DMAC_CTRL_LVLEN2 | DMAC_CTRL_LVLEN3;

	spi_dma_ready = true;
}


/**
 * Sends a block of data over the SPI bus using the DMAC.
 *
 * @return true if the transfer was performed by DMA, false if the caller should
 *         fall back to the polled path.
 */
static bool spi_send_dma(volatile sercom_t *sercom, spi_target_t port,
		const uint8_t *to_send, uint8_t *received, size_t length)
{
	// The DMAC's block transfer count is 16 bits, and very short transfers are
	// not worth the setup cost.
	if ((length < SPI_DMA_MIN_LENGTH) || (length > 0xFFFF)) {
		return false;
	}

	// Only the FPGA JTAG SERCOM has its trigger sources wired up here.
	if (port != SPI_FPGA_JTAG) {
		return false;
	}

	spi_dma_init();

	// Drain any byte left sitting in the receive register, so the first thing
	// the RX channel stores is the response to our first transmitted byte
	// rather than a stale leftover.
	while (sercom->SPI.INTFLAG.bit.RXC) {
		(void)sercom->SPI.DATA.reg;
	}

	// Receive descriptor: SERCOM DATA -> caller's buffer.
	//
	// DSTADDR must point one beat PAST the end of the destination block: the
	// DMAC decrements the address before each write when DSTINC is set. Getting
	// this wrong silently writes one byte outside the buffer.
	spi_dma_descriptors[SPI_DMA_CHANNEL_RX].BTCTRL.reg =
		DMAC_BTCTRL_VALID | DMAC_BTCTRL_BEATSIZE_BYTE |
		(received ? DMAC_BTCTRL_DSTINC : 0);
	spi_dma_descriptors[SPI_DMA_CHANNEL_RX].BTCNT.reg   = length;
	spi_dma_descriptors[SPI_DMA_CHANNEL_RX].SRCADDR.reg = (uint32_t)&sercom->SPI.DATA.reg;
	spi_dma_descriptors[SPI_DMA_CHANNEL_RX].DSTADDR.reg =
		received ? (uint32_t)(received + length) : (uint32_t)&spi_dma_scratch;
	spi_dma_descriptors[SPI_DMA_CHANNEL_RX].DESCADDR.reg = 0;

	// Transmit descriptor: caller's buffer -> SERCOM DATA. Same end-address
	// rule applies to SRCADDR when SRCINC is set.
	spi_dma_descriptors[SPI_DMA_CHANNEL_TX].BTCTRL.reg =
		DMAC_BTCTRL_VALID | DMAC_BTCTRL_BEATSIZE_BYTE | DMAC_BTCTRL_SRCINC;
	spi_dma_descriptors[SPI_DMA_CHANNEL_TX].BTCNT.reg   = length;
	spi_dma_descriptors[SPI_DMA_CHANNEL_TX].SRCADDR.reg = (uint32_t)(to_send + length);
	spi_dma_descriptors[SPI_DMA_CHANNEL_TX].DSTADDR.reg = (uint32_t)&sercom->SPI.DATA.reg;
	spi_dma_descriptors[SPI_DMA_CHANNEL_TX].DESCADDR.reg = 0;

	// Configure the receive channel first, and start it before the transmit
	// channel: it must already be armed when the first byte completes, or that
	// byte is lost and the transfer deadlocks waiting for a beat that never
	// arrives.
	//
	// Note the CHID indirection -- channel registers are a single window
	// selected by CHID, so this sequence is not re-entrant and must not be
	// touched from an interrupt.
	DMAC->CHID.reg = SPI_DMA_CHANNEL_RX;
	DMAC->CHCTRLA.reg = 0;
	while (DMAC->CHCTRLA.bit.ENABLE);
	DMAC->CHCTRLA.reg = DMAC_CHCTRLA_SWRST;
	while (DMAC->CHCTRLA.bit.SWRST);
	DMAC->CHCTRLB.reg = DMAC_CHCTRLB_TRIGSRC(SERCOM0_DMAC_ID_RX) |
			    DMAC_CHCTRLB_TRIGACT_BEAT | DMAC_CHCTRLB_LVL(3);
	DMAC->CHINTFLAG.reg = DMAC_CHINTFLAG_TCMPL | DMAC_CHINTFLAG_TERR;
	DMAC->CHCTRLA.reg = DMAC_CHCTRLA_ENABLE;

	DMAC->CHID.reg = SPI_DMA_CHANNEL_TX;
	DMAC->CHCTRLA.reg = 0;
	while (DMAC->CHCTRLA.bit.ENABLE);
	DMAC->CHCTRLA.reg = DMAC_CHCTRLA_SWRST;
	while (DMAC->CHCTRLA.bit.SWRST);
	DMAC->CHCTRLB.reg = DMAC_CHCTRLB_TRIGSRC(SERCOM0_DMAC_ID_TX) |
			    DMAC_CHCTRLB_TRIGACT_BEAT | DMAC_CHCTRLB_LVL(0);
	DMAC->CHINTFLAG.reg = DMAC_CHINTFLAG_TCMPL | DMAC_CHINTFLAG_TERR;
	DMAC->CHCTRLA.reg = DMAC_CHCTRLA_ENABLE;

	// Wait for the receive channel to retire. RX completes last by
	// construction -- the final byte cannot be received until after it has been
	// transmitted -- so this one flag covers the whole transaction, and means
	// the last byte is in memory before we return.
	DMAC->CHID.reg = SPI_DMA_CHANNEL_RX;
	while (!(DMAC->CHINTFLAG.reg & (DMAC_CHINTFLAG_TCMPL | DMAC_CHINTFLAG_TERR)));

	bool error = (DMAC->CHINTFLAG.reg & DMAC_CHINTFLAG_TERR) != 0;
	DMAC->CHINTFLAG.reg = DMAC_CHINTFLAG_TCMPL | DMAC_CHINTFLAG_TERR;
	DMAC->CHCTRLA.reg = 0;

	DMAC->CHID.reg = SPI_DMA_CHANNEL_TX;
	DMAC->CHINTFLAG.reg = DMAC_CHINTFLAG_TCMPL | DMAC_CHINTFLAG_TERR;
	DMAC->CHCTRLA.reg = 0;

	return !error;
}

/**
 * Returns the SERCOM object associated with the given target.
 */
static sercom_t *sercom_for_target(spi_target_t target)
{
	switch (target) {
		case SPI_FPGA_JTAG:  return SERCOM0; // Alternatively, SERCOM2.
		default:             return NULL;
	}
}


/**
 * Pinmux the relevent pins so the can be used for SERCOM SPI.
 */
static void _spi_configure_pinmux(spi_target_t target, bool use_for_spi)
{
	switch (target) {

		// FPGA JTAG connection -- configure PA08 (TDI), PA09 (TCK), and PA10 (TDO).
		case SPI_FPGA_JTAG:
			if (use_for_spi) {
				gpio_set_pin_function(PIN_PA14, MUX_PA14C_SERCOM0_PAD0);
				gpio_set_pin_function(PIN_PA15, MUX_PA15C_SERCOM0_PAD1);
				gpio_set_pin_function(PIN_PA10, MUX_PA10C_SERCOM0_PAD2);
			} else {
				gpio_set_pin_function(PIN_PA14, GPIO_PIN_FUNCTION_OFF);
				gpio_set_pin_function(PIN_PA15, GPIO_PIN_FUNCTION_OFF);
				gpio_set_pin_function(PIN_PA10, GPIO_PIN_FUNCTION_OFF);
			}
			break;

		default:
			// TODO
			break;
	}
}


/**
 * Configures the relevant SPI target's pins to be used for SPI.
 */
void spi_configure_pinmux(spi_target_t target)
{
	_spi_configure_pinmux(target, true);
}


/**
 * Returns the relevant SPI target's pins to being used for GPIO.
 */
void spi_release_pinmux(spi_target_t target)
{
	_spi_configure_pinmux(target, false);
}


/**
 * Configures the clocking for the relevant SERCOM peripheral.
 */
static void spi_set_up_clocking(spi_target_t target)
{
	switch (target) {

		case SPI_FPGA_JTAG:
			_pm_enable_bus_clock(PM_BUS_APBC, SERCOM0);
			_gclk_enable_channel(SERCOM0_GCLK_ID_CORE, GCLK_CLKCTRL_GEN_GCLK0_Val);
			break;

		case SPI_FPGA_DEBUG:
			_pm_enable_bus_clock(PM_BUS_APBC, SERCOM2);
			_gclk_enable_channel(SERCOM2_GCLK_ID_CORE, GCLK_CLKCTRL_GEN_GCLK0_Val);
			break;
	}

	// Wait for the clock to be ready.
	while(GCLK->STATUS.bit.SYNCBUSY);
}


/**
 * Configures the provided target to be used as an SPI port via the SERCOM.
 */
void spi_init(spi_target_t target, bool lsb_first, bool configure_pinmux, uint8_t baud_divider,
	 uint8_t clock_polarity, uint8_t clock_phase)
{
	volatile sercom_t *sercom = sercom_for_target(target);

	// Disable the SERCOM before configuring it, to 1) ensure we're not transacting
	// during configuration; and 2) as many of the registers are R/O when the SERCOM is enabled.
	while(sercom->SPI.SYNCBUSY.bit.ENABLE);
	sercom->SPI.CTRLA.bit.ENABLE = 0;

	// Software reset the SERCOM to restore initial values.
	while(sercom->SPI.SYNCBUSY.bit.SWRST);
	sercom->SPI.CTRLA.bit.SWRST = 1;

	// The SWRST bit becomes accessible again once the software reset is
	// complete -- we'll use this to wait for the reset to be finshed.
	while(sercom->SPI.SYNCBUSY.bit.SWRST);

	// Ensure we can work with the full SERCOM.
	while(sercom->SPI.SYNCBUSY.bit.SWRST || sercom->SPI.SYNCBUSY.bit.ENABLE);

	// Pinmux the relevant pins to be used for the SERCOM.
	if (configure_pinmux) {
		spi_configure_pinmux(target);
	}

	// Set up clocking for the SERCOM peripheral.
	spi_set_up_clocking(target);

	// Configure the SERCOM for SPI master mode.
	sercom->SPI.CTRLA.reg =
		SERCOM_SPI_CTRLA_MODE_SPI_MASTER  |  // SPI master
		SERCOM_SPI_CTRLA_DOPO(0)          |  // use our first pin as MOSI, and our second at SCK
		SERCOM_SPI_CTRLA_DIPO(2)          |  // use our third pin as MISO
		(lsb_first ? SERCOM_SPI_CTRLA_DORD : 0);   // SPI byte order

	// Set the clock polarity and phase.
	sercom->SPI.CTRLA.bit.CPOL = clock_polarity;
	sercom->SPI.CTRLA.bit.CPHA = clock_phase;

	// Use the SPI transceiver.
	while(sercom->SPI.SYNCBUSY.bit.CTRLB);
	sercom->SPI.CTRLB.reg = SERCOM_SPI_CTRLB_RXEN;

	// Set the baud divider for the relevant channel.
	sercom->SPI.BAUD.reg = baud_divider;

	// Finally, enable the SPI controller.
	sercom->SPI.CTRLA.reg |= SERCOM_SPI_CTRLA_ENABLE;
	while(sercom->SPI.SYNCBUSY.bit.ENABLE);
}


/**
 * Synchronously send a single byte on the given SPI bus.
 * Does not manage the SSEL line.
 */
uint8_t spi_send_byte(spi_target_t port, uint8_t data)
{
	volatile sercom_t *sercom = sercom_for_target(port);

	// Send the relevant data...
	while(sercom->SPI.INTFLAG.bit.DRE == 0);
	sercom->SPI.DATA.reg = data;

	// ... and receive the response.
	while(sercom->SPI.INTFLAG.bit.RXC == 0);
	return (uint8_t)sercom->SPI.DATA.reg;
}


/**
 * Sends a block of data over the SPI bus.
 *
 * @param port The port on which to perform the SPI transaction.
 * @param data_to_send The data to be transferred over the SPI bus.
 * @param data_received Any data received during the SPI transaction.
 * @param length The total length of the data to be exchanged, in bytes.
 */
void spi_send(spi_target_t port, void *data_to_send, void *data_received, size_t length)
{
	volatile sercom_t *sercom = sercom_for_target(port);
	uint8_t *to_send  = data_to_send;
	uint8_t *received = data_received;

	if (!length) {
		return;
	}

	// Prefer the DMAC, which clocks bytes back to back without per-byte CPU
	// work. It declines short transfers and non-JTAG targets, in which case we
	// fall through to the polled path below.
	if (spi_send_dma(sercom, port, to_send, received, length)) {
		return;
	}

	// Pipelined transfer. The SERCOM has a separate TX data-register-empty (DRE)
	// flag and receive-complete (RXC) flag, so once a byte has moved from DATA into
	// the shift register we may queue the next one while the first is still on the
	// wire. The naive loop (write, wait RXC, write, ...) leaves SCK idle between
	// bytes because it will not queue byte N+1 until byte N has fully returned.
	//
	// Ordering is what makes this correct: we prime the first byte, then for each
	// subsequent byte we wait for DRE and queue it BEFORE draining the previous
	// byte's RXC. That keeps exactly one byte queued behind the one in flight, and
	// because every queued byte is matched by exactly one blocking RXC read, no
	// received byte is ever dropped. (An earlier version polled DRE and RXC in a
	// single racy loop and corrupted TDO; the ECP5 then reported an all-zero
	// status register. Hence the strict ordering here.)
	while (sercom->SPI.INTFLAG.bit.DRE == 0);
	sercom->SPI.DATA.reg = to_send[0];

	for (size_t i = 1; i < length; ++i) {
		// Queue the next byte as soon as the shift register has taken the last one.
		while (sercom->SPI.INTFLAG.bit.DRE == 0);
		sercom->SPI.DATA.reg = to_send[i];

		// Now collect the byte that finished while we were queueing.
		while (sercom->SPI.INTFLAG.bit.RXC == 0);
		received[i - 1] = (uint8_t)sercom->SPI.DATA.reg;
	}

	// Drain the final byte, which has no successor to overlap with.
	while (sercom->SPI.INTFLAG.bit.RXC == 0);
	received[length - 1] = (uint8_t)sercom->SPI.DATA.reg;
}
