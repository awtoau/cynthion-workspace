//! Async FUSB302B sink PHY implementing [`usbpd_traits::Driver`].
//!
//! [`Fusb302b`] provides initialization, hardware GoodCRC/retries and FIFO
//! transport. A separate policy engine owns voltage selection and PD state.
//! Use [`Fusb302b::init_with_vbus_and_interrupt`] when the VBUS sense pin is
//! disconnected, and provide an external detector or the documented `()` bypass.
//! See the [README](https://github.com/okhsunrog/fusb302b#readme) for integration
//! examples and [design notes](https://github.com/okhsunrog/fusb302b/blob/main/docs/design.md)
//! for interrupt, framing and timing invariants.
#![cfg_attr(not(any(test, feature = "std")), no_std)]

#[cfg(all(feature = "defmt", feature = "log"))]
compile_error!("You may not enable both `defmt` and `log` features.");

mod interface;
mod packet;

pub use interface::DeviceInterface;
pub use packet::Sop;
use packet::{
    CRC_LEN, HEADER_LEN, Header, MAX_PAYLOAD_LEN, RX_PAYLOAD_OFFSET, RX_PREFIX_LEN, TxFrame,
    rx_sop,
};

use embedded_hal_async::i2c::I2c;
use thiserror::Error;

use embassy_time::{Duration, Instant, Timer, with_deadline};
use usbpd_traits::{Driver as SinkDriver, DriverRxError, DriverTxError};

// Existing driver scheduling budgets. These are not all USB-PD timer limits.
const RESET_SETTLE: Duration = Duration::from_millis(10);
const CC_MEASUREMENT_SETTLE: Duration = Duration::from_millis(10);
const IRQ_POLL_INTERVAL: Duration = Duration::from_millis(1);
const TX_COMPLETION_TIMEOUT: Duration = Duration::from_millis(15);
const HARD_RESET_TIMEOUT: Duration = Duration::from_millis(5);
const RX_POLL_TIMEOUT: Duration = Duration::from_millis(20);
// STATUS0A..INTERRUPT, including all three read-to-clear interrupt registers.
const STATUS_SNAPSHOT_LEN: usize = 7;

device_driver::compile!(
    options: "--rust-defmt-feature=defmt",
    manifest: "device.ddsl"
);
/// Default seven-bit I2C address supported by this driver.
pub const FUSB302B_I2C_ADDRESS: u8 = 0x22;

/// Convert BcLvl enum to comparable u8 value
fn bc_lvl_to_u8(lvl: BcLvl) -> u8 {
    match lvl {
        BcLvl::LessThan200MV => 0,
        BcLvl::Between200And660MV => 1,
        BcLvl::Between660And1230MV => 2,
        BcLvl::GreaterThan1230MV => 3,
    }
}

/// Transport and initialization errors.
#[derive(Debug, Error)]
#[cfg_attr(feature = "defmt", derive(defmt::Format))]
pub enum FusbError<I2cErr> {
    /// The underlying bus transaction failed.
    #[error("I2C error")]
    I2c(I2cErr),
    /// A write exceeds the transport's fixed software buffer.
    #[error("Data length exceeds internal buffer size for I2C transaction")]
    LenExceedsBuffer,
    /// The device reports a hardware version other than the supported A/B/C.
    #[error("Unsupported FUSB302B hardware version {0:#x}")]
    UnsupportedVersion(u8),
    /// Neither CC pin indicates an attached source during initialization.
    #[error("No source detected on either CC pin")]
    NoCcDetected,
}

/// Trait for custom VBUS detection. Implement this when VBUS is not
/// connected to the FUSB302B (e.g. EPR voltages >21V where an external
/// ADC with voltage divider is used instead).
pub trait VbusDetect {
    /// Wait until VBUS is present.
    fn wait_for_vbus(&mut self) -> impl core::future::Future<Output = ()>;
}

/// No-op VbusDetect impl for `()`, used as default generic parameter.
impl VbusDetect for () {
    async fn wait_for_vbus(&mut self) {}
}

enum VbusSource<V> {
    /// Use FUSB302B's built-in STATUS0.VBUSOK register.
    Internal,
    /// Use a user-provided external detector.
    External(V),
}

#[derive(Default)]
struct Events {
    hard_reset: bool,
    tx_sent: bool,
    retry_fail: bool,
    hard_sent: bool,
    collision: bool,
}

/// Trait for waiting on the FUSB302B INT_N pin.
/// INT_N is open-drain, active-low: it asserts (goes LOW) when any unmasked
/// interrupt flag is set, and returns HIGH once all flags are cleared.
///
/// The default `()` implementation falls back to 1ms polling (no pin needed).
pub trait InterruptPin {
    /// Wait for an interrupt event. For a real GPIO pin this should wait
    /// until INT_N is low. For the `()` fallback this sleeps 1ms.
    fn wait_for_interrupt(&mut self) -> impl core::future::Future<Output = ()>;
}

/// Polling fallback: sleep 1ms between register reads (original behavior).
impl InterruptPin for () {
    async fn wait_for_interrupt(&mut self) {
        Timer::after(IRQ_POLL_INTERVAL).await;
    }
}

/// Wrapper to use an `embedded_hal_async::digital::Wait` GPIO pin as an interrupt source.
/// The pin should be connected to the FUSB302B INT_N output with an external pull-up.
pub struct GpioInterrupt<P>(pub P);

impl<P: embedded_hal_async::digital::Wait> InterruptPin for GpioInterrupt<P> {
    async fn wait_for_interrupt(&mut self) {
        // wait_for_low returns immediately if already low (per embedded-hal-async spec),
        // which is correct for the level-triggered INT_N signal.
        let _ = self.0.wait_for_low().await;
    }
}

/// Async sink PHY with hardware GoodCRC and retries.
///
/// `V` selects external VBUS detection and `I` selects IRQ waiting. Their `()`
/// defaults mean an internal VBUS check and polling respectively. Initialization
/// selects CC once; dynamic attachment/orientation tracking is not provided.
/// Constructors return [`FusbError::NoCcDetected`] when neither pin indicates
/// a source, or [`FusbError::UnsupportedVersion`] for an unknown hardware version.
pub struct Fusb302b<I2CBus, E, V = (), I = ()>
where
    I2CBus: I2c<Error = E>,
    E: core::fmt::Debug,
{
    /// Generated register API. Changes must preserve the active PHY configuration.
    pub ll: FusbLowLevel<DeviceInterface<I2CBus>>,
    vbus_source: VbusSource<V>,
    int_pin: I,
    events: Events,
    _marker: core::marker::PhantomData<E>,
}

/// Detected CC pin orientation
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "defmt", derive(defmt::Format))]
pub enum CcPin {
    /// Configuration channel 1.
    Cc1,
    /// Configuration channel 2.
    Cc2,
}

impl<I2CBus, E> Fusb302b<I2CBus, E, (), ()>
where
    I2CBus: I2c<Error = E> + 'static,
    E: core::fmt::Debug,
{
    /// Initialize with built-in VBUS detection and polling (no interrupt pin).
    pub async fn init(i2c: I2CBus) -> Result<Self, FusbError<E>> {
        Self::initialize(i2c, VbusSource::Internal, ()).await
    }
}

impl<I2CBus, E, I> Fusb302b<I2CBus, E, (), I>
where
    I2CBus: I2c<Error = E> + 'static,
    E: core::fmt::Debug,
    I: InterruptPin,
{
    /// Initialize with built-in VBUS detection and an interrupt pin.
    pub async fn init_with_interrupt_pin(i2c: I2CBus, int_pin: I) -> Result<Self, FusbError<E>> {
        Self::initialize(i2c, VbusSource::Internal, int_pin).await
    }
}

impl<I2CBus, E, V> Fusb302b<I2CBus, E, V, ()>
where
    I2CBus: I2c<Error = E> + 'static,
    E: core::fmt::Debug,
    V: VbusDetect,
{
    /// Initialize with a custom VBUS detection strategy and polling (no interrupt pin).
    pub async fn init_with_vbus_detect(i2c: I2CBus, vbus_detect: V) -> Result<Self, FusbError<E>> {
        Self::initialize(i2c, VbusSource::External(vbus_detect), ()).await
    }
}

impl<I2CBus, E, V, I> Fusb302b<I2CBus, E, V, I>
where
    I2CBus: I2c<Error = E> + 'static,
    E: core::fmt::Debug,
    V: VbusDetect,
    I: InterruptPin,
{
    /// Initialize with both a custom VBUS detection strategy and an interrupt pin.
    pub async fn init_with_vbus_and_interrupt(
        i2c: I2CBus,
        vbus_detect: V,
        int_pin: I,
    ) -> Result<Self, FusbError<E>> {
        Self::initialize(i2c, VbusSource::External(vbus_detect), int_pin).await
    }
}

impl<I2CBus, E, I> Fusb302b<I2CBus, E, (), I>
where
    I2CBus: I2c<Error = E> + 'static,
    E: core::fmt::Debug,
    I: InterruptPin,
{
    /// Take over a part the caller has already configured (awto patch, source role).
    ///
    /// No reset and no CC detection: CC pull-ups, `SWITCHES0/1` (roles, `TXCC`),
    /// `AUTO_CRC`, retries and masks are the caller's. Only the version is checked,
    /// and SOP'/SOP'' reception is enabled.
    pub async fn attach_configured(i2c: I2CBus, int_pin: I) -> Result<Self, FusbError<E>> {
        let mut driver = Self {
            ll: FusbLowLevel::new(DeviceInterface::new(i2c)),
            vbus_source: VbusSource::Internal,
            int_pin,
            events: Events::default(),
            _marker: core::marker::PhantomData,
        };
        let id = driver.ll.device_id().read_async().await?;
        if let Fusb302Version::UnknownOrFutureVersion(version) = id.version_id() {
            return Err(FusbError::UnsupportedVersion(version));
        }
        driver
            .ll
            .control_1()
            .modify_async(|r| {
                r.set_ensop_1(true);
                r.set_ensop_2(true);
            })
            .await?;
        Ok(driver)
    }
}

impl<I2CBus, E, V, I> Fusb302b<I2CBus, E, V, I>
where
    I2CBus: I2c<Error = E> + 'static,
    E: core::fmt::Debug,
{
    async fn initialize(
        i2c: I2CBus,
        vbus_source: VbusSource<V>,
        int_pin: I,
    ) -> Result<Self, FusbError<E>> {
        let mut driver = Self {
            ll: FusbLowLevel::new(DeviceInterface::new(i2c)),
            vbus_source,
            int_pin,
            events: Events::default(),
            _marker: core::marker::PhantomData,
        };
        driver.init_hardware().await?;
        Ok(driver)
    }

    /// Shared hardware initialization sequence.
    async fn init_hardware(&mut self) -> Result<(), FusbError<E>> {
        // Fully reset the FUSB302B
        self.ll.reset().write_async(|r| r.set_sw_res(true)).await?;
        Timer::after(RESET_SETTLE).await;

        // Verify device is responding
        let device_id = self.ll.device_id().read_async().await?;
        // Check that we get a valid known version (A, B, or C)
        match device_id.version_id() {
            Fusb302Version::VersionA | Fusb302Version::VersionB | Fusb302Version::VersionC => {}
            Fusb302Version::UnknownOrFutureVersion(version) => {
                return Err(FusbError::UnsupportedVersion(version));
            }
        }

        // Keep the PHY powered for the active session. Toggling POWER around
        // each transmission adds four I2C transactions to the response path.
        self.ll
            .power()
            .write_async(|r| {
                r.set_pwr_0_bandgap_and_wake_enable(true);
                r.set_pwr_1_receiver_and_measure_refs_enable(true);
                r.set_pwr_2_measure_block_power_enable(true);
                r.set_pwr_3_internal_oscillator_enable(true);
            })
            .await?;

        // Set interrupt masks to 0 (all interrupts enabled)
        self.ll.mask().write_async(|r| *r = Mask::default()).await?;
        self.ll
            .maska()
            .write_async(|r| *r = Maska::default())
            .await?;
        self.ll
            .maskb()
            .write_async(|r| *r = Maskb::default())
            .await?;

        // Unmask interrupts
        self.ll
            .control_0()
            .write_async(|r| r.set_int_mask(false))
            .await?;

        // Configure hardware auto-retry: 2 retries per USB PD spec (nRetryCount = 2)
        self.ll
            .control_3()
            .write_async(|r| {
                r.set_auto_retry(true);
                r.set_n_retries(RetryCount::TwoRetries);
                r.set_auto_softreset(false); // Let policy engine handle
                r.set_auto_hardreset(false); // Let policy engine handle
            })
            .await?;

        // Flush the RX buffer
        self.ll
            .control_1()
            .write_async(|r| r.set_rx_flush(true))
            .await?;

        // Detect and select CC line
        self.detect_cc_pin().await?;

        // Reset PD logic
        self.ll
            .reset()
            .write_async(|r| r.set_pd_reset(true))
            .await?;

        // From this point the peer may receive GoodCRC and start its response
        // timer. Never reset/flush RX after enabling automatic acknowledgements.
        self.ll
            .switches_1()
            .modify_async(|r| r.set_auto_crc(true))
            .await?;

        Ok(())
    }

    /// Detect which CC pin is connected and configure switches accordingly
    async fn detect_cc_pin(&mut self) -> Result<CcPin, FusbError<E>> {
        self.select_cc_measurement(CcPin::Cc1).await?;

        Timer::after(CC_MEASUREMENT_SETTLE).await;

        let cc1 = bc_lvl_to_u8(self.ll.status_0().read_async().await?.bc_lvl());

        self.select_cc_measurement(CcPin::Cc2).await?;

        Timer::after(CC_MEASUREMENT_SETTLE).await;

        let cc2 = bc_lvl_to_u8(self.ll.status_0().read_async().await?.bc_lvl());

        if cc1 == 0 && cc2 == 0 {
            return Err(FusbError::NoCcDetected);
        }

        // Select the CC line with higher voltage (indicates connection)
        let selected_cc = if cc1 > cc2 { CcPin::Cc1 } else { CcPin::Cc2 };

        let cc1_selected = selected_cc == CcPin::Cc1;
        // Leave automatic acknowledgement disabled until the final PD reset.
        // Keep the reset-default GoodCRC revision (2.0): USB PD 3.2 section
        // 6.2.1.1.5 permits this even when the peer uses revision 3.x.
        self.ll
            .switches_1()
            .write_async(|r| {
                r.set_txcc_1(cc1_selected);
                r.set_txcc_2(!cc1_selected);
                r.set_auto_crc(false);
            })
            .await?;
        self.select_cc_measurement(selected_cc).await?;

        Ok(selected_cc)
    }

    /// Present Rd on both CC pins and route the measurement block to one pin.
    async fn select_cc_measurement(&mut self, cc: CcPin) -> Result<(), FusbError<E>> {
        self.ll
            .switches_0()
            .write_async(|r| {
                r.set_pdwn_1(true);
                r.set_pdwn_2(true);
                r.set_meas_cc_1(cc == CcPin::Cc1);
                r.set_meas_cc_2(cc == CcPin::Cc2);
            })
            .await
    }

    /// Read the device's hardware version, product and revision fields.
    pub async fn get_device_info(&mut self) -> Result<DeviceId, FusbError<E>> {
        self.ll.device_id().read_async().await
    }

    // All three interrupt registers are read-to-clear. Retain events needed by
    // another operation even if a future is cancelled between I2C transactions.
    async fn read_events(&mut self) -> Result<Status1, FusbError<E>> {
        // Like IronOS, use one burst, not separate transactions for each IRQ.
        // I_GCRCSENT must be cleared too, otherwise INT_N stays low forever.
        let mut status = [0; STATUS_SNAPSHOT_LEN];
        self.ll.status_snapshot().read_async(&mut status).await?;
        let [_status0a, _status1a, irq_a, _irq_b, _status0, status1, irq] = status;
        let irq_a = Interrupta::from([irq_a]);
        let irq = Interrupt::from([irq]);
        self.events.hard_reset |= irq_a.i_hardrst();
        self.events.tx_sent |= irq_a.i_txsent();
        self.events.retry_fail |= irq_a.i_retryfail();
        self.events.hard_sent |= irq_a.i_hardsent();
        self.events.collision |= irq.i_collision();
        Ok(Status1::from([status1]))
    }

    async fn reset_pd_and_fifos(&mut self) -> Result<(), FusbError<E>> {
        self.ll
            .control_0()
            .modify_async(|r| r.set_tx_flush(true))
            .await?;
        self.ll
            .control_1()
            .modify_async(|r| r.set_rx_flush(true))
            .await?;
        self.ll
            .reset()
            .write_async(|r| r.set_pd_reset(true))
            .await?;
        self.events = Events::default();
        Ok(())
    }
}

impl<I2CBus, E, V, I> SinkDriver for Fusb302b<I2CBus, E, V, I>
where
    I2CBus: I2c<Error = E> + 'static,
    E: core::fmt::Debug,
    V: VbusDetect,
    I: InterruptPin,
{
    const HAS_AUTO_GOOD_CRC: bool = true;
    const HAS_AUTO_RETRY: bool = true;

    async fn wait_for_vbus(&mut self) {
        match &mut self.vbus_source {
            VbusSource::Internal => {
                // Poll STATUS0.VBUSOK (R-only, bit 7) until VBUS is above ~4.0V.
                loop {
                    if let Ok(status0) = self.ll.status_0().read_async().await
                        && status0.vbusok()
                    {
                        return;
                    }
                    // Drain every unmasked source before waiting for INT_N.
                    let _ = self.read_events().await;
                    self.int_pin.wait_for_interrupt().await;
                }
            }
            VbusSource::External(v) => v.wait_for_vbus().await,
        }
    }

    async fn transmit_hard_reset(&mut self) -> Result<(), DriverTxError> {
        self.reset_pd_and_fifos()
            .await
            .map_err(|_| DriverTxError::Discarded)?;
        self.ll
            .control_3()
            .modify_async(|r| r.set_send_hard_reset(true))
            .await
            .map_err(|_| DriverTxError::Discarded)?;

        let deadline = Instant::now() + HARD_RESET_TIMEOUT;
        loop {
            self.read_events()
                .await
                .map_err(|_| DriverTxError::Discarded)?;
            if self.events.hard_reset {
                return Err(DriverTxError::HardReset);
            }
            if core::mem::take(&mut self.events.hard_sent) {
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(DriverTxError::Discarded);
            }
            let _ = with_deadline(deadline, self.int_pin.wait_for_interrupt()).await;
        }
    }

    async fn transmit(&mut self, data: &[u8]) -> Result<(), DriverTxError> {
        self.transmit_sop(Sop::Sop, data).await
    }

    async fn receive(&mut self, buffer: &mut [u8]) -> Result<usize, DriverRxError> {
        loop {
            let (length, sop) = self.receive_sop(buffer).await?;
            if sop == Sop::Sop {
                return Ok(length);
            }
        }
    }
}

impl<I2CBus, E, V, I> Fusb302b<I2CBus, E, V, I>
where
    I2CBus: I2c<Error = E> + 'static,
    E: core::fmt::Debug,
    V: VbusDetect,
    I: InterruptPin,
{
    /// Transmit one message with the given start-of-packet (awto patch).
    pub async fn transmit_sop(&mut self, sop: Sop, data: &[u8]) -> Result<(), DriverTxError> {
        let frame = TxFrame::new(sop, data).ok_or(DriverTxError::Discarded)?;
        self.read_events()
            .await
            .map_err(|_| DriverTxError::Discarded)?;
        if self.events.hard_reset {
            self.reset_pd_and_fifos()
                .await
                .map_err(|_| DriverTxError::Discarded)?;
            return Err(DriverTxError::HardReset);
        }
        // Completion from a previous cancelled/failed transmission is stale.
        self.events.tx_sent = false;
        self.events.retry_fail = false;
        self.events.collision = false;
        self.ll
            .control_0()
            .modify_async(|r| r.set_tx_flush(true))
            .await
            .map_err(|_| DriverTxError::Discarded)?;
        self.ll
            .fifo()
            .write_all_async(frame.as_bytes())
            .await
            .map_err(|_| DriverTxError::Discarded)?;

        // Allow the initial attempt and two hardware retries to complete.
        let deadline = Instant::now() + TX_COMPLETION_TIMEOUT;
        loop {
            self.read_events()
                .await
                .map_err(|_| DriverTxError::Discarded)?;
            if self.events.hard_reset {
                self.reset_pd_and_fifos()
                    .await
                    .map_err(|_| DriverTxError::Discarded)?;
                return Err(DriverTxError::HardReset);
            }
            if core::mem::take(&mut self.events.tx_sent) {
                return Ok(());
            }
            if core::mem::take(&mut self.events.retry_fail) {
                // Hardware auto-retry exhausted all attempts without receiving GoodCRC
                return Err(DriverTxError::Discarded);
            }
            if core::mem::take(&mut self.events.collision) {
                return Err(DriverTxError::Discarded);
            }
            if Instant::now() >= deadline {
                return Err(DriverTxError::Discarded);
            }
            let _ = with_deadline(deadline, self.int_pin.wait_for_interrupt()).await;
        }
    }

    /// Receive one non-GoodCRC message of any start-of-packet (awto patch).
    pub async fn receive_sop(&mut self, buffer: &mut [u8]) -> Result<(usize, Sop), DriverRxError> {
        if buffer.len() < HEADER_LEN {
            return Err(DriverRxError::Discarded);
        }
        let deadline = Instant::now() + RX_POLL_TIMEOUT;
        loop {
            let status1 = self
                .read_events()
                .await
                .map_err(|_| DriverRxError::Discarded)?;
            if self.events.hard_reset {
                self.reset_pd_and_fifos()
                    .await
                    .map_err(|_| DriverRxError::Discarded)?;
                return Err(DriverRxError::HardReset);
            }
            // Use RX_EMPTY from STATUS1 (R-only) instead of I_CRC_CHK from
            // INTERRUPT (R/C). The INTERRUPT register is read-cleared, and
            // transmit() reads it to check I_COLLISION — which clears I_CRC_CHK
            // as a side effect, causing a race condition.
            if !status1.rx_empty() {
                let (length, sop) = self.read_frame(buffer).await?;
                let header = Header::from_bytes([buffer[0], buffer[1]]);
                // AUTO_RETRY already consumed this acknowledgement logically.
                // FUSB302 still puts it in RX FIFO. Never deliver it to the PE:
                // Ready would reply NotSupported, creating an endless exchange.
                if header.is_good_crc() {
                    if Instant::now() >= deadline {
                        return Err(DriverRxError::Discarded);
                    }
                    continue;
                }
                return Ok((length, sop));
            }
            if Instant::now() >= deadline {
                return Err(DriverRxError::Discarded);
            }
            let _ = with_deadline(deadline, self.int_pin.wait_for_interrupt()).await;
        }
    }
}

impl<I2CBus, E, V, I> Fusb302b<I2CBus, E, V, I>
where
    I2CBus: I2c<Error = E> + 'static,
    E: core::fmt::Debug,
{
    async fn read_frame(&mut self, buffer: &mut [u8]) -> Result<(usize, Sop), DriverRxError> {
        // Every complete frame has at least token + header + CRC (7 bytes).
        // Read that minimum together so control packets, including the
        // hardware retry engine's GoodCRC, need only one FIFO transaction.
        let mut prefix = [0u8; RX_PREFIX_LEN];
        self.ll
            .fifo()
            .read_async(&mut prefix)
            .await
            .map_err(|_| DriverRxError::Discarded)?;

        // Check if the received byte is a valid Start-of-Packet token.
        let Some(sop) = rx_sop(prefix[0]) else {
            self.ll
                .control_1()
                .modify_async(|r| r.set_rx_flush(true))
                .await
                .ok();
            return Err(DriverRxError::Discarded);
        };

        // The I2C buffer interface always fills the whole slice, so a single
        // read is an exact read here.
        let header_buf = [prefix[1], prefix[2]];
        let header = Header::from_bytes(header_buf);
        let payload_len = header.payload_len();
        let total_len = HEADER_LEN + payload_len;

        if total_len > buffer.len() {
            self.ll
                .control_1()
                .modify_async(|r| r.set_rx_flush(true))
                .await
                .ok();
            return Err(DriverRxError::Discarded);
        }

        buffer[..HEADER_LEN].copy_from_slice(&header_buf);
        if payload_len != 0 {
            // The minimum control-frame read contains four payload bytes for
            // a data frame. Those bytes occupy the control frame's CRC slots.
            let first_payload = &prefix[RX_PAYLOAD_OFFSET..];
            let remaining_payload_start = HEADER_LEN + first_payload.len();
            buffer[HEADER_LEN..remaining_payload_start].copy_from_slice(first_payload);
            // Consume only this frame's remaining payload and CRC. Reading
            // farther would steal the next packet's SOP token.
            let remaining_payload_len = payload_len - first_payload.len();
            let tail_read_len = remaining_payload_len + CRC_LEN;
            let mut tail = [0u8; MAX_PAYLOAD_LEN];
            self.ll
                .fifo()
                .read_async(&mut tail[..tail_read_len])
                .await
                .map_err(|_| DriverRxError::Discarded)?;
            buffer[remaining_payload_start..total_len]
                .copy_from_slice(&tail[..remaining_payload_len]);
        }

        Ok((total_len, sop))
    }
}
