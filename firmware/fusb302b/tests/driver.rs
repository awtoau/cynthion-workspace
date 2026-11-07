use core::convert::Infallible;
use std::{
    collections::VecDeque,
    sync::{Arc, Mutex},
    time::Duration,
};

use embedded_hal_async::i2c::{ErrorType, I2c, Operation};
use fusb302b::{Fusb302b, FusbError, InterruptPin};
use usbpd_traits::{Driver, DriverRxError, DriverTxError};

struct Model {
    registers: [u8; 256],
    fifo: VecDeque<u8>,
    reads: [usize; 256],
    tx_event: u8,
    tx_good_crc: bool,
    stall_fifo_reset: bool,
    frame_on_auto_crc: Option<Vec<u8>>,
    tx_frames: Vec<Vec<u8>>,
    cc_levels: [u8; 2],
}

impl Default for Model {
    fn default() -> Self {
        let mut registers = [0; 256];
        registers[1] = 0x91;
        registers[2] = 3;
        registers[3] = 0x20;
        Self {
            registers,
            fifo: VecDeque::new(),
            reads: [0; 256],
            tx_event: 4,
            tx_good_crc: false,
            stall_fifo_reset: false,
            frame_on_auto_crc: None,
            tx_frames: Vec::new(),
            cc_levels: [2, 0],
        }
    }
}

impl Model {
    fn push_frame(&mut self, bytes: &[u8]) {
        self.fifo.push_back(0xe0);
        self.fifo.extend(bytes);
        self.fifo.extend([0; 4]); // CRC checked by the PHY.
    }

    fn read(&mut self, address: u8, out: &mut [u8]) {
        self.reads[usize::from(address)] += 1;
        if address == 0x43 {
            for b in out {
                *b = self.fifo.pop_front().expect("RX FIFO underflow");
            }
            return;
        }
        for (offset, b) in out.iter_mut().enumerate() {
            let reg = usize::from(address) + offset;
            if offset != 0 {
                self.reads[reg] += 1;
            }
            *b = match reg {
                // Select CC1; VBUS sense stays low even though the board is powered.
                0x40 => {
                    if self.registers[2] & 4 != 0 {
                        self.cc_levels[0]
                    } else {
                        self.cc_levels[1]
                    }
                }
                0x41 => {
                    if self.fifo.is_empty() {
                        0x20
                    } else {
                        0
                    }
                }
                _ => self.registers[reg],
            };
            if [0x3e, 0x3f, 0x42].contains(&reg) {
                self.registers[reg] = 0;
            }
        }
    }

    fn write(&mut self, address: u8, data: &[u8]) {
        if address == 0x43 {
            self.tx_frames.push(data.to_vec());
            self.registers[0x3e] |= self.tx_event;
            if self.tx_good_crc {
                self.push_frame(&[0x41, 0x01]);
            }
            return;
        }
        for (offset, &b) in data.iter().enumerate() {
            let reg = usize::from(address) + offset;
            self.registers[reg] = b;
            if reg == 3
                && b & 4 != 0
                && let Some(frame) = self.frame_on_auto_crc.take()
            {
                self.push_frame(&frame);
            }
            if reg == 0x0c && b & 2 != 0 {
                self.fifo.clear();
            }
            if reg == 7 && b & 4 != 0 {
                self.fifo.clear();
            }
            // Model self-clearing FIFO flush and hard-reset command bits.
            if reg == 6 {
                self.registers[reg] &= !0x40;
            }
            if reg == 7 {
                self.registers[reg] &= !4;
            }
            if reg == 9 && b & 0x40 != 0 {
                self.registers[reg] &= !0x40;
                self.registers[0x3e] |= self.tx_event & 8;
            }
        }
    }
}

#[derive(Clone, Default)]
struct Bus(Arc<Mutex<Model>>);
impl ErrorType for Bus {
    type Error = Infallible;
}
impl I2c for Bus {
    async fn transaction(
        &mut self,
        address: u8,
        operations: &mut [Operation<'_>],
    ) -> Result<(), Self::Error> {
        assert_eq!(address, 0x22);
        tokio::task::yield_now().await;
        let stall = self.0.lock().unwrap().stall_fifo_reset
            && matches!(operations.first(), Some(Operation::Write(bytes)) if *bytes == [6]);
        if stall {
            core::future::pending::<()>().await;
        }
        let mut model = self.0.lock().unwrap();
        let mut register = 0;
        for op in operations {
            match op {
                Operation::Write(bytes) => {
                    register = bytes[0];
                    if bytes.len() > 1 {
                        model.write(register, &bytes[1..]);
                    }
                }
                Operation::Read(bytes) => model.read(register, bytes),
            }
        }
        Ok(())
    }
}

// No interrupt ever arrives. Every operation must still honor its deadline.
struct SilentIrq;
impl InterruptPin for SilentIrq {
    async fn wait_for_interrupt(&mut self) {
        core::future::pending().await
    }
}

async fn driver() -> (Fusb302b<Bus, Infallible, (), SilentIrq>, Bus) {
    let bus = Bus::default();
    let driver = Fusb302b::init_with_vbus_and_interrupt(bus.clone(), (), SilentIrq)
        .await
        .unwrap();
    (driver, bus)
}

#[tokio::test]
async fn initialization_does_not_flush_a_packet_it_has_already_acknowledged() {
    let bus = Bus::default();
    let frame = [0xa1, 0x11, 0x2c, 0x91, 0x01, 0x27];
    bus.0.lock().unwrap().frame_on_auto_crc = Some(frame.to_vec());
    let mut driver = Fusb302b::init_with_vbus_and_interrupt(bus, (), SilentIrq)
        .await
        .unwrap();
    let mut out = [0; 30];
    assert_eq!(driver.receive(&mut out).await.unwrap(), frame.len());
    assert_eq!(&out[..frame.len()], &frame);
}

#[tokio::test]
async fn skips_good_crc_but_preserves_following_policy_message_and_clears_all_irqs() {
    let (mut driver, bus) = driver().await;
    {
        let mut m = bus.0.lock().unwrap();
        m.registers[0x3f] = 1; // I_GCRCSENT holds INT_N low until read.
        m.registers[0x42] = 0x10;
        m.push_frame(&[0x41, 0x01]); // GoodCRC belongs to hardware retry.
        m.push_frame(&[0x43, 0x03]); // Accept belongs to the policy engine.
    }
    let mut buffer = [0; 30];
    assert_eq!(driver.receive(&mut buffer).await.unwrap(), 2);
    assert_eq!(&buffer[..2], &[0x43, 0x03]);
    let m = bus.0.lock().unwrap();
    assert!(m.fifo.is_empty());
    assert_eq!(m.reads[0x43], 2, "one FIFO transaction per control frame");
    for r in [0x3e, 0x3f, 0x42] {
        assert!(m.reads[r] > 0);
        assert_eq!(m.registers[r], 0);
    }
}

#[tokio::test]
async fn receive_timeout_does_not_require_a_gpio_edge() {
    let (mut driver, _) = driver().await;
    let result =
        tokio::time::timeout(Duration::from_millis(200), driver.receive(&mut [0; 30])).await;
    assert!(matches!(result, Ok(Err(DriverRxError::Discarded))));
}

#[tokio::test]
async fn transmit_and_hard_reset_timeout_without_a_gpio_edge() {
    let (mut driver, bus) = driver().await;
    bus.0.lock().unwrap().tx_event = 0;
    let tx = tokio::time::timeout(Duration::from_millis(200), driver.transmit(&[0x80, 0])).await;
    assert!(matches!(tx, Ok(Err(DriverTxError::Discarded))));
    let reset =
        tokio::time::timeout(Duration::from_millis(200), driver.transmit_hard_reset()).await;
    assert!(matches!(reset, Ok(Err(DriverTxError::Discarded))));
}

#[tokio::test]
async fn stale_tx_completion_cannot_acknowledge_a_new_transmission() {
    let (mut driver, bus) = driver().await;
    {
        let mut m = bus.0.lock().unwrap();
        m.tx_event = 0;
        m.registers[0x3e] = 4;
    }
    assert!(matches!(
        driver.transmit(&[0x80, 0]).await,
        Err(DriverTxError::Discarded)
    ));
}

#[tokio::test]
async fn hardware_tx_success_does_not_leak_its_ack_to_the_policy_engine() {
    let (mut driver, bus) = driver().await;
    bus.0.lock().unwrap().tx_good_crc = true;
    driver.transmit(&[0x80, 0]).await.unwrap();
    bus.0.lock().unwrap().push_frame(&[0x46, 0x03]); // PS_RDY immediately after GoodCRC.
    let mut buffer = [0; 30];
    assert_eq!(driver.receive(&mut buffer).await.unwrap(), 2);
    assert_eq!(&buffer[..2], &[0x46, 0x03]);
}

#[tokio::test]
async fn transmit_keeps_the_active_phy_powered_without_extra_power_register_reads() {
    let (mut driver, bus) = driver().await;
    let before = bus.0.lock().unwrap().reads[0x0b];
    driver.transmit(&[0x80, 0]).await.unwrap();
    let model = bus.0.lock().unwrap();
    assert_eq!(model.registers[0x0b], 0x0f);
    assert_eq!(model.reads[0x0b], before);
}

#[tokio::test]
async fn reads_one_object_without_consuming_the_following_control_frame() {
    let (mut driver, bus) = driver().await;
    let frame = [0x8a, 0x11, 0, 0, 0x54, 1];
    {
        let mut model = bus.0.lock().unwrap();
        model.push_frame(&frame);
        model.push_frame(&[0x43, 0x03]);
    }
    let mut buffer = [0; 30];
    assert_eq!(driver.receive(&mut buffer).await.unwrap(), frame.len());
    assert_eq!(&buffer[..frame.len()], &frame);
    assert_eq!(driver.receive(&mut buffer).await.unwrap(), 2);
    assert_eq!(&buffer[..2], &[0x43, 0x03]);
    let model = bus.0.lock().unwrap();
    assert!(model.fifo.is_empty());
    assert_eq!(model.reads[0x43], 3);
}

#[tokio::test]
async fn received_hard_reset_flushes_stale_packets_and_precedes_tx_success() {
    let (mut driver, bus) = driver().await;
    {
        let mut m = bus.0.lock().unwrap();
        m.registers[0x3e] = 1 | 4;
        m.push_frame(&[0x43, 0x03]);
    }
    assert!(matches!(
        driver.transmit(&[0x80, 0]).await,
        Err(DriverTxError::HardReset)
    ));
    assert!(bus.0.lock().unwrap().fifo.is_empty());
}

#[tokio::test]
async fn external_vbus_detector_does_not_read_the_unconnected_vbus_pin() {
    let (mut driver, bus) = driver().await;
    let before = bus.0.lock().unwrap().reads[0x40];
    tokio::time::timeout(Duration::from_millis(100), driver.wait_for_vbus())
        .await
        .unwrap();
    assert_eq!(before, bus.0.lock().unwrap().reads[0x40]);
}

#[tokio::test]
async fn clears_good_crc_sent_interrupt_even_when_rx_fifo_is_empty() {
    let (mut driver, bus) = driver().await;
    bus.0.lock().unwrap().registers[0x3f] = 1;
    assert!(matches!(
        driver.receive(&mut [0; 30]).await,
        Err(DriverRxError::Discarded)
    ));
    let m = bus.0.lock().unwrap();
    assert!(m.reads[0x3f] > 0);
    assert_eq!(m.registers[0x3f], 0);
}

#[tokio::test]
async fn cancelling_reset_cleanup_does_not_lose_a_hard_reset() {
    let (mut driver, bus) = driver().await;
    {
        let mut m = bus.0.lock().unwrap();
        m.registers[0x3e] = 1;
        m.stall_fifo_reset = true;
    }
    assert!(
        tokio::time::timeout(Duration::from_millis(10), driver.receive(&mut [0; 30]))
            .await
            .is_err()
    );
    {
        let mut m = bus.0.lock().unwrap();
        assert!(m.reads[0x3e] > 0);
        m.stall_fifo_reset = false;
    }
    assert!(matches!(
        driver.transmit(&[0x80, 0]).await,
        Err(DriverTxError::HardReset)
    ));
}

#[tokio::test]
async fn reads_maximum_size_frame_and_preserves_the_next_packet() {
    let (mut driver, bus) = driver().await;
    let mut frame = [0; 30];
    frame[..2].copy_from_slice(&[0x81, 0x71]); // Seven data objects.
    for (i, byte) in frame[2..].iter_mut().enumerate() {
        *byte = i as u8;
    }
    {
        let mut m = bus.0.lock().unwrap();
        m.push_frame(&frame);
        m.push_frame(&[0x43, 0x03]);
    }
    let mut buffer = [0; 30];
    assert_eq!(driver.receive(&mut buffer).await.unwrap(), 30);
    assert_eq!(buffer, frame);
    assert_eq!(driver.receive(&mut buffer).await.unwrap(), 2);
    assert_eq!(&buffer[..2], &[0x43, 0x03]);
    assert!(bus.0.lock().unwrap().fifo.is_empty());
}

#[tokio::test]
async fn malformed_buffer_sizes_do_not_panic_or_write_the_fifo() {
    let (mut driver, bus) = driver().await;
    assert!(matches!(
        driver.transmit(&[0; 40]).await,
        Err(DriverTxError::Discarded)
    ));
    assert!(matches!(
        driver.transmit(&[]).await,
        Err(DriverTxError::Discarded)
    ));
    assert!(matches!(
        driver.receive(&mut [0; 1]).await,
        Err(DriverRxError::Discarded)
    ));
    assert!(bus.0.lock().unwrap().fifo.is_empty());
}

#[tokio::test]
async fn transmit_encodes_control_and_maximum_data_frames_in_one_write_each() {
    let (mut driver, bus) = driver().await;
    let control = [0x80, 0];
    let mut data = [0x5a; 30];
    data[..2].copy_from_slice(&[0x81, 0x71]);
    for message in [&control[..], &data[..]] {
        driver.transmit(message).await.unwrap();
    }
    let m = bus.0.lock().unwrap();
    assert_eq!(m.tx_frames.len(), 2);
    assert_eq!(
        m.tx_frames[0],
        [
            0x12, 0x12, 0x12, 0x13, 0x82, 0x80, 0, 0xff, 0x14, 0xfe, 0xa1
        ]
    );
    assert_eq!(&m.tx_frames[1][..5], &[0x12, 0x12, 0x12, 0x13, 0x9e]);
    assert_eq!(&m.tx_frames[1][5..35], &data);
    assert_eq!(&m.tx_frames[1][35..], &[0xff, 0x14, 0xfe, 0xa1]);
}

#[tokio::test]
async fn all_object_counts_preserve_the_next_queued_packet_and_fifo_read_count() {
    let (mut driver, bus) = driver().await;
    for count in 0..=7u8 {
        let mut frame = vec![0x82, count << 4];
        frame.extend((0..usize::from(count) * 4).map(|i| i as u8));
        let before = {
            let mut m = bus.0.lock().unwrap();
            m.push_frame(&frame);
            m.push_frame(&[0x43, 0x03]);
            m.reads[0x43]
        };
        let mut buffer = [0; 30];
        let len = driver.receive(&mut buffer).await.unwrap();
        assert_eq!(&buffer[..len], &frame);
        assert_eq!(driver.receive(&mut buffer).await.unwrap(), 2);
        assert_eq!(&buffer[..2], &[0x43, 0x03]);
        let m = bus.0.lock().unwrap();
        assert!(m.fifo.is_empty());
        assert_eq!(m.reads[0x43] - before, if count == 0 { 2 } else { 3 });
    }
}

#[tokio::test]
async fn data_or_extended_type_one_messages_are_not_discarded_as_good_crc() {
    let (mut driver, bus) = driver().await;
    for frame in [vec![0x81, 0x11, 1, 2, 3, 4], vec![0x81, 0x80]] {
        bus.0.lock().unwrap().push_frame(&frame);
        let mut buffer = [0; 30];
        let len = driver.receive(&mut buffer).await.unwrap();
        assert_eq!(&buffer[..len], &frame);
    }
}

#[tokio::test]
async fn non_sop_tokens_and_undersized_output_flush_only_the_current_rx_queue() {
    let (mut driver, bus) = driver().await;
    {
        let mut m = bus.0.lock().unwrap();
        m.push_frame(&[0x43, 0x03]);
        m.fifo[0] = 0xc0; // SOP', not supported by this sink PHY.
    }
    assert!(matches!(
        driver.receive(&mut [0; 30]).await,
        Err(DriverRxError::Discarded)
    ));
    assert!(bus.0.lock().unwrap().fifo.is_empty());
    bus.0.lock().unwrap().push_frame(&[0x81, 0x11, 1, 2, 3, 4]);
    assert!(matches!(
        driver.receive(&mut [0; 2]).await,
        Err(DriverRxError::Discarded)
    ));
    let mut buffer = [0; 2];
    bus.0.lock().unwrap().push_frame(&[0x43, 0x03]);
    assert_eq!(driver.receive(&mut buffer).await.unwrap(), 2);
    assert_eq!(buffer, [0x43, 0x03]);
}

#[tokio::test]
async fn initialization_reports_the_unsupported_hardware_version() {
    let bus = Bus::default();
    bus.0.lock().unwrap().registers[1] = 0xf1;
    assert!(matches!(
        Fusb302b::init(bus).await,
        Err(FusbError::UnsupportedVersion(15))
    ));
}

#[tokio::test]
async fn initialization_requires_a_source_before_enabling_acknowledgements() {
    let bus = Bus::default();
    bus.0.lock().unwrap().cc_levels = [0, 0];
    assert!(matches!(
        Fusb302b::init_with_vbus_and_interrupt(bus.clone(), (), SilentIrq).await,
        Err(FusbError::NoCcDetected)
    ));
    assert_eq!(bus.0.lock().unwrap().registers[3] & 4, 0);
}

#[tokio::test]
async fn constructors_select_either_cc_orientation_and_preserve_good_crc_revision_two() {
    let cc1 = Bus::default();
    Fusb302b::init(cc1.clone()).await.unwrap();
    assert_eq!(cc1.0.lock().unwrap().registers[2], 0x07);
    assert_eq!(cc1.0.lock().unwrap().registers[3], 0x25);

    let cc2 = Bus::default();
    cc2.0.lock().unwrap().cc_levels = [0, 2];
    Fusb302b::init_with_interrupt_pin(cc2.clone(), SilentIrq)
        .await
        .unwrap();
    assert_eq!(cc2.0.lock().unwrap().registers[2], 0x0b);
    assert_eq!(cc2.0.lock().unwrap().registers[3], 0x26);

    Fusb302b::init_with_vbus_detect(Bus::default(), ())
        .await
        .unwrap();
}

#[tokio::test]
async fn attach_configured_keeps_the_callers_registers_and_enables_sop_prime() {
    let bus = Bus::default();
    {
        let mut m = bus.0.lock().unwrap();
        m.registers[2] = 0x44; // Rp on CC1, measure CC1: the caller's setup.
        m.registers[3] = 0b1011_0101; // source, DFP, AUTO_CRC, TXCC1.
    }
    let _driver = Fusb302b::attach_configured(bus.clone(), SilentIrq)
        .await
        .unwrap();
    let m = bus.0.lock().unwrap();
    assert_eq!(m.registers[2], 0x44);
    assert_eq!(m.registers[3], 0b1011_0101);
    assert_eq!(m.registers[7] & 3, 3, "ENSOP1 and ENSOP2");
    assert_eq!(m.reads[0x0c], 0, "no reset");
}

#[tokio::test]
async fn transmit_sop_uses_the_sop_prime_and_double_prime_sync_sets() {
    let (mut driver, bus) = driver().await;
    driver
        .transmit_sop(fusb302b::Sop::SopPrime, &[0x80, 0])
        .await
        .unwrap();
    driver
        .transmit_sop(fusb302b::Sop::SopDoublePrime, &[0x80, 0])
        .await
        .unwrap();
    let m = bus.0.lock().unwrap();
    assert_eq!(&m.tx_frames[0][..5], &[0x12, 0x12, 0x1b, 0x1b, 0x82]);
    assert_eq!(&m.tx_frames[1][..5], &[0x12, 0x1b, 0x12, 0x1b, 0x82]);
}

#[tokio::test]
async fn receive_sop_reports_the_token_and_receive_skips_cable_frames() {
    let (mut driver, bus) = driver().await;
    {
        let mut m = bus.0.lock().unwrap();
        m.push_frame(&[0x47, 0x02]);
        m.fifo[0] = 0xc0; // SOP'
        m.push_frame(&[0x43, 0x03]); // SOP
    }
    let mut buffer = [0; 30];
    assert_eq!(
        driver.receive_sop(&mut buffer).await.unwrap(),
        (2, fusb302b::Sop::SopPrime)
    );
    assert_eq!(&buffer[..2], &[0x47, 0x02]);
    {
        let mut m = bus.0.lock().unwrap();
        let at = m.fifo.len();
        m.push_frame(&[0x47, 0x02]);
        m.fifo[at] = 0xa0; // SOP''
        m.push_frame(&[0x43, 0x03]);
    }
    assert_eq!(driver.receive(&mut buffer).await.unwrap(), 2);
    assert_eq!(&buffer[..2], &[0x43, 0x03], "SOP frame after the cable one");
}
