//! moondancer's USB control-transfer path, ported to this SoC.
//!
//! The source is `greatscottgadgets/cynthion`: `firmware/smolusb/src/{setup,
//! event,traits,control,device}.rs` and the `MachineExternal` handler at the top
//! of `firmware/moondancer/src/bin/moondancer.rs`. Vendored rather than depended
//! on, which is what `docs/upstream-boundary.md` says to do ("do not inherit a
//! stack to get one file"). Both trees are BSD-3-Clause.
//!
//! Included by `#[path]` from `src/bin/usb_rtic.rs` and `src/bin/usb_bare.rs`.
//! `src/main.rs` does not declare it, so it is not in the shipping image.
//!
//! ## What is real and what is a stand-in
//!
//! **This SoC has no USB device controller.** The gateware's only USB peripheral
//! is `ulpi_window.UlpiRegisters`, a four-register window that reads and writes a
//! USB3343's PHY registers; it cannot send or receive a packet. LUNA's
//! `USBSerialDevice` on AUX is hard-wired to the 16550 and exposes no CSR, no bus
//! and no interrupt. There is no SETUP FIFO here to read a packet out of.
//!
//! So:
//!
//! | piece | here |
//! |---|---|
//! | [`SetupPacket`] and its accessors | **real**, smolusb's `setup.rs` |
//! | [`UsbEvent`] | **real**, smolusb's `event.rs` |
//! | [`Control::dispatch_event`] | **real**, smolusb's `control.rs` state machine |
//! | the descriptor request dispatch | **real** shape, byte tables instead of `zerocopy` views |
//! | the interrupt that starts it | **real** -- a 16550 RX line through the controller |
//! | the FIFO the handler reads the packet out of | **stand-in** -- the 16550's RBR, not an EP_CONTROL |
//! | what the endpoint writes go to | **stand-in** -- [`Endpoints`], a RAM buffer and a trace |
//!
//! The state machine does not know the difference: it is written against
//! [`UsbDriver`], which is why it ports at all.
//!
//! ## The wire frames
//!
//! Bytes arriving on the console are read as moondancer's own event encoding --
//! smolusb's `From<UsbEvent> for [u8; 2]`, with the SETUP payload appended:
//!
//! | first byte | then | event |
//! |---|---|---|
//! | 10 | -- | `BusReset` |
//! | 12 | ep | `ReceivePacket(ep)` |
//! | 13 | ep | `SendComplete(ep)` |
//! | 201 | ep, 8 bytes | `ReceiveSetupPacket(ep, packet)` |
//! | 255 | -- | report and stop |
//!
//! ## What was adapted, and why
//!
//! - **No `log`.** smolusb calls `error!`/`warn!`/`trace!` throughout; this
//!   firmware has no logging crate and a handler here may not print at all
//!   (`scripts/soc_irq_log_check.py`), so each call site became a counter in
//!   [`Trace`], printed by idle. Nothing silently dropped.
//! - **Slices, not iterators.** smolusb's `write_requested` takes an
//!   `Iterator<Item = u8>` because its descriptors are `zerocopy` views over
//!   packed structs. Byte tables need no view, so the trait takes `&[u8]` --
//!   same truncation rule, same return value.
//! - **No Microsoft OS 1.0 branch, no string table indirection.** Both are
//!   descriptor content, not control flow.

use core::sync::atomic::{AtomicU32, AtomicU8, AtomicUsize, Ordering};

// - smolusb::setup -----------------------------------------------------------

/// A USB setup packet, as it arrives on the wire.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct SetupPacket {
    pub request_type: u8,
    pub request: u8,
    pub value: u16,
    pub index: u16,
    pub length: u16,
}

impl From<[u8; 8]> for SetupPacket {
    fn from(b: [u8; 8]) -> Self {
        // Field by field rather than smolusb's `transmute`, which its own
        // comment calls "the most cursed manner available to us". The layout is
        // little-endian on both machines, so the result is the same; this one
        // does not depend on that being true.
        SetupPacket {
            request_type: b[0],
            request: b[1],
            value: u16::from_le_bytes([b[2], b[3]]),
            index: u16::from_le_bytes([b[4], b[5]]),
            length: u16::from_le_bytes([b[6], b[7]]),
        }
    }
}

impl SetupPacket {
    pub fn request_type(&self) -> RequestType {
        RequestType::from(self.request_type)
    }
    pub fn recipient(&self) -> Recipient {
        Recipient::from(self.request_type)
    }
    pub fn direction(&self) -> Direction {
        Direction::from(self.request_type)
    }
    pub fn request(&self) -> Request {
        Request::from(self.request)
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Recipient {
    Device,
    Interface,
    Endpoint,
    Other,
    Reserved,
}

impl From<u8> for Recipient {
    fn from(value: u8) -> Self {
        match value & 0b0001_1111 {
            0 => Recipient::Device,
            1 => Recipient::Interface,
            2 => Recipient::Endpoint,
            3 => Recipient::Other,
            _ => Recipient::Reserved,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum RequestType {
    Standard,
    Class,
    Vendor,
    Reserved,
}

impl From<u8> for RequestType {
    fn from(value: u8) -> Self {
        match (value >> 5) & 0b11 {
            0 => RequestType::Standard,
            1 => RequestType::Class,
            2 => RequestType::Vendor,
            _ => RequestType::Reserved,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    HostToDevice,
    DeviceToHost,
}

impl From<u8> for Direction {
    fn from(value: u8) -> Self {
        if value & 0b1000_0000 == 0 {
            Direction::HostToDevice
        } else {
            Direction::DeviceToHost
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Request {
    GetStatus,
    ClearFeature,
    SetFeature,
    SetAddress,
    GetDescriptor,
    SetDescriptor,
    GetConfiguration,
    SetConfiguration,
    GetInterface,
    SetInterface,
    SynchronizeFrame,
    ClassOrVendor(u8),
    Reserved(u8),
}

impl From<u8> for Request {
    fn from(value: u8) -> Self {
        match value {
            0 => Request::GetStatus,
            1 => Request::ClearFeature,
            3 => Request::SetFeature,
            5 => Request::SetAddress,
            6 => Request::GetDescriptor,
            7 => Request::SetDescriptor,
            8 => Request::GetConfiguration,
            9 => Request::SetConfiguration,
            10 => Request::GetInterface,
            11 => Request::SetInterface,
            12 => Request::SynchronizeFrame,
            2 | 4 => Request::Reserved(value),
            _ => Request::ClassOrVendor(value),
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Feature {
    EndpointHalt,
    DeviceRemoteWakeup,
    Unknown,
}

impl From<u16> for Feature {
    fn from(value: u16) -> Self {
        match value {
            0 => Feature::EndpointHalt,
            1 => Feature::DeviceRemoteWakeup,
            _ => Feature::Unknown,
        }
    }
}

// - smolusb::event -----------------------------------------------------------

/// What the USB peripheral's interrupt handler produces.
///
/// `ReceiveControl` is in smolusb and not here: it is the variant for a driver
/// that leaves the packet in the FIFO, and moondancer's handler reads it in the
/// handler instead, which is the path being ported.
#[derive(Clone, Copy)]
pub enum UsbEvent {
    BusReset,
    ReceivePacket(u8),
    SendComplete(u8),
    ReceiveSetupPacket(u8, SetupPacket),
}

impl UsbEvent {
    /// smolusb's own discriminants, and what the wire frames use.
    pub fn code(&self) -> u8 {
        match self {
            UsbEvent::BusReset => 10,
            UsbEvent::ReceivePacket(_) => 12,
            UsbEvent::SendComplete(_) => 13,
            UsbEvent::ReceiveSetupPacket(_, _) => 201,
        }
    }
}

// - smolusb::traits ----------------------------------------------------------

/// What the control state machine needs a USB peripheral to do.
///
/// smolusb splits this across `UsbDriverOperations`, `ReadControl`,
/// `ReadEndpoint` and `WriteEndpoint`. One trait here: the split exists so a
/// driver can implement the read half without the write half, and there is one
/// implementation on this machine.
pub trait UsbDriver {
    fn bus_reset(&mut self);
    fn set_address(&mut self, address: u8);
    fn stall_endpoint_in(&mut self, endpoint: u8);
    fn stall_endpoint_out(&mut self, endpoint: u8);
    fn clear_feature_endpoint_halt(&mut self, endpoint: u8, direction: Direction);
    fn ep_out_prime_receive(&mut self, endpoint: u8);
    /// Bytes read. Zero is a ZLP, which several states depend on.
    fn read(&mut self, endpoint: u8, buffer: &mut [u8]) -> usize;
    /// Bytes written.
    fn write(&mut self, endpoint: u8, data: &[u8]) -> usize;
    /// Write no more than the host asked for. smolusb spells the truncation as
    /// `.take(requested_length)` on the iterator.
    fn write_requested(&mut self, endpoint: u8, requested: usize, data: &[u8]) -> usize {
        let n = data.len().min(requested);
        self.write(endpoint, &data[..n])
    }
}

// - descriptors --------------------------------------------------------------

/// Largest packet the control endpoint takes. `smolusb::max_packet_size` returns
/// this for endpoint 0 at every speed.
pub const EP0_MAX_PACKET_SIZE: usize = 64;

/// Cynthion's device descriptor, from `moondancer::usb::DEVICE_DESCRIPTOR` and
/// `cynthion/rust/src/shared/usb.toml`: 0x1d50/0x615b, OpenMoko's vendor id.
#[rustfmt::skip]
static DEVICE_DESCRIPTOR: [u8; 18] = [
    18, 0x01,               // bLength, bDescriptorType
    0x00, 0x02,             // bcdUSB 2.00
    0x00, 0x00, 0x00,       // class, subclass, protocol -- composite
    64,                     // bMaxPacketSize0
    0x50, 0x1d,             // idVendor
    0x5b, 0x61,             // idProduct
    0x04, 0x01,             // bcdDevice 1.04
    1, 2, 3,                // iManufacturer, iProduct, iSerialNumber
    1,                      // bNumConfigurations
];

/// One configuration, one interface, two bulk endpoints -- moondancer's shape.
/// `bInterfaceSubClass` 0x20 is what `usb.toml` reserves for Moondancer.
#[rustfmt::skip]
static CONFIGURATION_DESCRIPTOR: [u8; 32] = [
    9, 0x02, 32, 0,         // bLength, type, wTotalLength
    1, 1, 0,                // bNumInterfaces, bConfigurationValue, iConfiguration
    0x80, 250,              // bmAttributes, bMaxPower (500 mA)
    9, 0x04, 0, 0, 2,       // interface 0, alt 0, two endpoints
    0xff, 0x20, 0x00, 0,    // vendor class, moondancer subclass, protocol, iInterface
    7, 0x05, 0x81, 0x02, 0x00, 0x02, 0,   // EP1 IN, bulk, 512
    7, 0x05, 0x02, 0x02, 0x00, 0x02, 0,   // EP2 OUT, bulk, 512
];

/// String descriptor zero: the supported language list. 0x0409 is en-US.
static STRING_DESCRIPTOR_0: [u8; 4] = [4, 0x03, 0x09, 0x04];

/// "Cynthion Project", UTF-16LE. iManufacturer, iProduct and iSerialNumber all
/// resolve here -- the real table has three entries and one of them is built at
/// run time from the flash UUID, which is descriptor content and not control
/// flow.
#[rustfmt::skip]
static STRING_DESCRIPTOR_1: [u8; 34] = [
    34, 0x03,
    b'C', 0, b'y', 0, b'n', 0, b't', 0, b'h', 0, b'i', 0, b'o', 0, b'n', 0,
    b' ', 0, b'P', 0, b'r', 0, b'o', 0, b'j', 0, b'e', 0, b'c', 0, b't', 0,
];

/// smolusb's `Descriptors::write`, over byte tables.
///
/// Same contract as upstream: writes the descriptor and returns `None`, or
/// returns the setup packet unconsumed so the caller can try to handle it.
fn write_descriptor<D: UsbDriver>(
    usb: &mut D,
    endpoint: u8,
    setup: SetupPacket,
    trace: &Trace,
) -> Option<SetupPacket> {
    let [number, kind] = setup.value.to_le_bytes();
    let requested = setup.length as usize;

    let table: &[u8] = match (kind, number) {
        (0x01, 0) => &DEVICE_DESCRIPTOR,
        (0x02, _) => &CONFIGURATION_DESCRIPTOR,
        (0x03, 0) => &STRING_DESCRIPTOR_0,
        (0x03, 1..=3) => &STRING_DESCRIPTOR_1,
        // A high-speed device with no qualifier acks instead of stalling, which
        // upstream marks FIXME and this keeps: changing it here would be a
        // change to the behaviour under test.
        (0x06, _) | (0x07, _) => {
            usb.write(endpoint, &[]);
            return None;
        }
        _ => {
            trace.unhandled_descriptor.fetch_add(1, Ordering::Relaxed);
            return Some(setup);
        }
    };

    usb.write_requested(endpoint, requested, table);
    None
}

// - smolusb::control ---------------------------------------------------------

/// The control interface's state, between one event and the next.
#[derive(Clone, Copy)]
pub enum State {
    Idle,
    Send,
    WaitForZlp,
    SetAddress(u8),
    ReceiveHostData(SetupPacket),
    FinishHostData(SetupPacket),
    Complete,
    Stall,
}

impl State {
    /// A small integer, so a [`Journal`] record is four bytes and the name is
    /// looked up where printing is allowed.
    pub fn index(&self) -> u8 {
        match self {
            State::Idle => 0,
            State::Send => 1,
            State::WaitForZlp => 2,
            State::SetAddress(_) => 3,
            State::ReceiveHostData(_) => 4,
            State::FinishHostData(_) => 5,
            State::Complete => 6,
            State::Stall => 7,
        }
    }
}

/// State names, indexed by [`State::index`].
pub const STATE_NAMES: [&str; 8] = [
    "Idle",
    "Send",
    "WaitForZlp",
    "SetAddress",
    "ReceiveHostData",
    "FinishHostData",
    "Complete",
    "Stall",
];

/// What smolusb says with `log`. Counters, because a handler here may not print
/// and because the shell's own deferred log (`src/events.rs`) is not linked into
/// a spike.
#[derive(Default)]
pub struct Trace {
    pub unhandled_descriptor: AtomicU32,
    pub unknown_configuration: AtomicU32,
    pub unhandled_feature: AtomicU32,
    pub expected_zlp: AtomicU32,
    pub receive_overflow: AtomicU32,
    pub length_mismatch: AtomicU32,
    pub state_error: AtomicU32,
}

/// smolusb's control endpoint, ported.
///
/// `RX` is `LIBGREAT_MAX_COMMAND_SIZE` upstream, where it holds a whole GCP
/// command; a const generic for the same reason.
pub struct Control<const RX: usize> {
    endpoint_number: u8,
    pub next: State,
    pub configuration: Option<u8>,
    feature_remote_wakeup: bool,
    rx_buffer: [u8; RX],
    rx_buffer_position: usize,
    pub trace: Trace,
    /// Events this state machine has been handed. The direct evidence that
    /// dispatch happened at all.
    pub dispatched: AtomicU32,
}

impl<const RX: usize> Control<RX> {
    pub fn new(endpoint_number: u8) -> Self {
        Control {
            endpoint_number,
            next: State::Idle,
            configuration: None,
            feature_remote_wakeup: false,
            rx_buffer: [0; RX],
            rx_buffer_position: 0,
            trace: Trace::default(),
            dispatched: AtomicU32::new(0),
        }
    }

    pub fn data(&self) -> &[u8] {
        &self.rx_buffer[..self.rx_buffer_position]
    }

    fn write_zlp<D: UsbDriver>(&self, usb: &mut D) {
        usb.write(self.endpoint_number, &[]);
    }

    fn read_zlp<D: UsbDriver>(&self, usb: &mut D) -> bool {
        usb.read(self.endpoint_number, &mut [0; EP0_MAX_PACKET_SIZE]) == 0
    }

    /// The state machine, arm for arm from `smolusb::control::Control`.
    ///
    /// Returns a setup packet the control interface could not handle -- a class
    /// or vendor request -- which is where moondancer's
    /// `handle_vendor_request` picks it up.
    pub fn dispatch_event<D: UsbDriver>(
        &mut self,
        usb: &mut D,
        event: UsbEvent,
    ) -> Option<SetupPacket> {
        self.dispatched.fetch_add(1, Ordering::Relaxed);
        let ep = self.endpoint_number;

        match (event, self.next) {
            (UsbEvent::BusReset, _) => {
                self.next = State::Idle;
            }

            (UsbEvent::ReceiveSetupPacket(n, setup), State::Idle | State::Stall) if n == ep => {
                self.next = State::Idle;
                let requested = setup.length as usize;

                match (setup.direction(), setup.request_type(), setup.request()) {
                    (Direction::DeviceToHost, RequestType::Standard, Request::GetDescriptor) => {
                        self.next = State::Send;
                        return write_descriptor(usb, ep, setup, &self.trace);
                    }
                    (Direction::HostToDevice, RequestType::Standard, Request::SetAddress) => {
                        self.next = State::SetAddress((setup.value & 0x7f) as u8);
                        self.write_zlp(usb);
                    }
                    (Direction::HostToDevice, RequestType::Standard, Request::SetConfiguration) => {
                        let configuration = (setup.value & 0xff) as u8;
                        if configuration > 1 {
                            self.trace
                                .unknown_configuration
                                .fetch_add(1, Ordering::Relaxed);
                            self.configuration = None;
                            self.next = State::Stall;
                            usb.stall_endpoint_out(ep);
                            return None;
                        }
                        self.configuration = Some(configuration);
                        self.next = State::Complete;
                        self.write_zlp(usb);
                    }
                    (Direction::DeviceToHost, RequestType::Standard, Request::GetConfiguration) => {
                        self.next = State::Send;
                        usb.write(ep, &[self.configuration.unwrap_or(0)]);
                    }
                    (Direction::DeviceToHost, RequestType::Standard, Request::GetStatus) => {
                        // bit 0 self-powered, bit 1 remote-wakeup
                        let status: u16 = 0b01 | (u16::from(self.feature_remote_wakeup) << 1);
                        self.next = State::Send;
                        usb.write(ep, &status.to_le_bytes());
                    }
                    (_, RequestType::Standard, Request::ClearFeature) => {
                        match (setup.recipient(), Feature::from(setup.value)) {
                            (Recipient::Endpoint, Feature::EndpointHalt) => {
                                let address = (setup.index & 0xff) as u8;
                                usb.clear_feature_endpoint_halt(
                                    address & 0x7f,
                                    Direction::from(address),
                                );
                                self.next = State::Complete;
                                self.write_zlp(usb);
                            }
                            (Recipient::Device, Feature::DeviceRemoteWakeup) => {
                                self.feature_remote_wakeup = false;
                                self.next = State::Complete;
                                self.write_zlp(usb);
                            }
                            _ => {
                                self.trace.unhandled_feature.fetch_add(1, Ordering::Relaxed);
                                self.next = State::Stall;
                                usb.stall_endpoint_in(ep);
                            }
                        }
                    }
                    (_, RequestType::Standard, Request::SetFeature) => {
                        self.next = State::Complete;
                        match (setup.recipient(), Feature::from(setup.value)) {
                            (Recipient::Device, Feature::DeviceRemoteWakeup) => {
                                self.feature_remote_wakeup = true;
                                self.write_zlp(usb);
                            }
                            _ => {
                                self.trace.unhandled_feature.fetch_add(1, Ordering::Relaxed);
                                usb.stall_endpoint_in(ep);
                                self.next = State::Stall;
                            }
                        }
                    }

                    // Unsupported, but the host has data for us: read it before
                    // handing the request on.
                    (Direction::HostToDevice, _, _) if setup.length > 0 => {
                        self.rx_buffer_position = 0;
                        self.next = State::ReceiveHostData(setup);
                        usb.ep_out_prime_receive(ep);
                    }

                    // Unsupported. The caller gets it -- a vendor request.
                    _ => {
                        let _ = requested;
                        self.next = State::Idle;
                        return Some(setup);
                    }
                }
            }

            (UsbEvent::SendComplete(n), State::Send) if n == ep => {
                self.next = State::WaitForZlp;
                usb.ep_out_prime_receive(ep);
            }

            // Part of a multi-packet send; safely ignored.
            (UsbEvent::SendComplete(n), State::WaitForZlp) if n == ep => {}

            (UsbEvent::ReceivePacket(n), State::WaitForZlp) if n == ep => {
                self.next = State::Idle;
                if !self.read_zlp(usb) {
                    self.trace.expected_zlp.fetch_add(1, Ordering::Relaxed);
                }
            }

            (UsbEvent::SendComplete(n), State::SetAddress(address)) if n == ep => {
                self.next = State::Idle;
                usb.set_address(address);
            }

            (UsbEvent::SendComplete(n), State::Complete) if n == ep => {
                self.next = State::Idle;
            }

            (UsbEvent::ReceivePacket(n), State::ReceiveHostData(setup)) if n == ep => {
                let mut packet = [0_u8; EP0_MAX_PACKET_SIZE];
                let read = usb.read(ep, &mut packet);

                if read == 0 {
                    // Early abort. We are done with what we have.
                    self.next = State::FinishHostData(setup);
                    self.write_zlp(usb);
                    return None;
                }
                if self.rx_buffer_position + read > RX {
                    self.trace.receive_overflow.fetch_add(1, Ordering::Relaxed);
                    self.next = State::ReceiveHostData(setup);
                    usb.ep_out_prime_receive(ep);
                    return None;
                }

                let at = self.rx_buffer_position;
                self.rx_buffer[at..at + read].copy_from_slice(&packet[..read]);
                self.rx_buffer_position += read;

                if self.rx_buffer_position >= usize::from(setup.length) {
                    self.next = State::FinishHostData(setup);
                    self.write_zlp(usb);
                } else {
                    self.next = State::ReceiveHostData(setup);
                    usb.ep_out_prime_receive(ep);
                }
            }

            (UsbEvent::SendComplete(n), State::FinishHostData(setup)) if n == ep => {
                self.next = State::Idle;
                if self.rx_buffer_position != usize::from(setup.length) {
                    self.trace.length_mismatch.fetch_add(1, Ordering::Relaxed);
                }
                return Some(setup);
            }

            // Something wrote to the endpoint outside this interface.
            (UsbEvent::ReceivePacket(n), State::Idle) if n == ep => {
                if !self.read_zlp(usb) {
                    self.trace.expected_zlp.fetch_add(1, Ordering::Relaxed);
                }
            }
            (UsbEvent::SendComplete(n), State::Idle) if n == ep => {}

            _ => {
                self.next = State::Idle;
                self.trace.state_error.fetch_add(1, Ordering::Relaxed);
            }
        }

        None
    }
}

// - the endpoint stand-in ----------------------------------------------------

/// Where the endpoint writes go, on a machine with no endpoints.
///
/// It records rather than transmits. The state machine above cannot tell -- the
/// return values are the same ones a real driver gives -- which is the whole
/// reason a port was possible; it is also exactly the part of moondancer that
/// this SoC cannot run.
pub struct Endpoints {
    pub address: Option<u8>,
    pub bytes_written: u32,
    pub writes: u32,
    pub zlps: u32,
    pub primes: u32,
    pub stalls: u32,
    pub halts_cleared: u32,
    /// The first and last things written, so a test can assert on the bytes
    /// rather than only on a count. The first non-empty write of an enumeration
    /// is the device descriptor, which is the one worth checking against the
    /// specification.
    pub first: [u8; EP0_MAX_PACKET_SIZE],
    pub first_len: usize,
    pub last: [u8; EP0_MAX_PACKET_SIZE],
    pub last_len: usize,
    /// What the host has queued for the next OUT read, set by the frame decoder.
    pub host_data: [u8; EP0_MAX_PACKET_SIZE],
    pub host_data_len: usize,
}

impl Endpoints {
    pub fn new() -> Self {
        Endpoints {
            address: None,
            bytes_written: 0,
            writes: 0,
            zlps: 0,
            primes: 0,
            stalls: 0,
            halts_cleared: 0,
            first: [0; EP0_MAX_PACKET_SIZE],
            first_len: 0,
            last: [0; EP0_MAX_PACKET_SIZE],
            last_len: 0,
            host_data: [0; EP0_MAX_PACKET_SIZE],
            host_data_len: 0,
        }
    }
}

impl UsbDriver for Endpoints {
    fn bus_reset(&mut self) {
        self.address = None;
    }
    fn set_address(&mut self, address: u8) {
        self.address = Some(address);
    }
    fn stall_endpoint_in(&mut self, _endpoint: u8) {
        self.stalls += 1;
    }
    fn stall_endpoint_out(&mut self, _endpoint: u8) {
        self.stalls += 1;
    }
    fn clear_feature_endpoint_halt(&mut self, _endpoint: u8, _direction: Direction) {
        self.halts_cleared += 1;
    }
    fn ep_out_prime_receive(&mut self, _endpoint: u8) {
        self.primes += 1;
    }

    fn read(&mut self, _endpoint: u8, buffer: &mut [u8]) -> usize {
        let n = self.host_data_len.min(buffer.len());
        buffer[..n].copy_from_slice(&self.host_data[..n]);
        // Consumed: a FIFO read pops. The next read is a ZLP unless the host
        // queues more, which is what a real OUT endpoint does.
        self.host_data_len = 0;
        n
    }

    fn write(&mut self, _endpoint: u8, data: &[u8]) -> usize {
        self.writes += 1;
        if data.is_empty() {
            self.zlps += 1;
        }
        self.bytes_written += data.len() as u32;
        let n = data.len().min(self.last.len());
        self.last[..n].copy_from_slice(&data[..n]);
        self.last_len = n;
        if self.first_len == 0 && n > 0 {
            self.first[..n].copy_from_slice(&data[..n]);
            self.first_len = n;
        }
        data.len()
    }
}

// - the frame decoder --------------------------------------------------------

/// How many bytes the longest frame is: 201, endpoint, eight of setup packet.
const FRAME_MAX: usize = 10;

/// Assembles console bytes into [`UsbEvent`]s.
///
/// This is where moondancer's `get_usb_interrupt_event` reads eight bytes out of
/// `USB0_EP_CONTROL` and builds a `ReceiveSetupPacket`, and it runs in the same
/// place: inside the interrupt handler, for the same stated reason. What it
/// reads from is the difference.
///
/// Not `Sync`-by-atomics for elegance -- the handler is the only writer, and on
/// this machine cannot preempt itself -- but because a `static` shared with a
/// handler must not be a `static mut`, which `scripts/soc_irq_log_check.py`
/// refuses crate-wide.
pub struct Frames {
    bytes: [AtomicU8; FRAME_MAX],
    len: AtomicUsize,
}

impl Frames {
    pub const fn new() -> Self {
        Frames {
            bytes: [const { AtomicU8::new(0) }; FRAME_MAX],
            len: AtomicUsize::new(0),
        }
    }

    /// How many bytes the frame starting with `code` needs in total.
    fn width(code: u8) -> usize {
        match code {
            10 | 255 => 1,
            12 | 13 => 2,
            201 => 10,
            // Unknown: consume the byte alone rather than waiting for a length
            // that will never arrive.
            _ => 1,
        }
    }

    /// Feed one byte. `Some` when it completed a frame.
    pub fn push(&self, byte: u8) -> Option<Framed> {
        let len = self.len.load(Ordering::Relaxed);
        self.bytes[len].store(byte, Ordering::Relaxed);
        let len = len + 1;
        let code = self.bytes[0].load(Ordering::Relaxed);

        if len < Self::width(code) {
            self.len.store(len, Ordering::Relaxed);
            return None;
        }
        self.len.store(0, Ordering::Relaxed);

        let at = |i: usize| self.bytes[i].load(Ordering::Relaxed);
        match code {
            10 => Some(Framed::Event(UsbEvent::BusReset)),
            12 => Some(Framed::Event(UsbEvent::ReceivePacket(at(1)))),
            13 => Some(Framed::Event(UsbEvent::SendComplete(at(1)))),
            201 => {
                let mut raw = [0_u8; 8];
                for (i, slot) in raw.iter_mut().enumerate() {
                    *slot = at(2 + i);
                }
                Some(Framed::Event(UsbEvent::ReceiveSetupPacket(
                    at(1),
                    SetupPacket::from(raw),
                )))
            }
            255 => Some(Framed::Report),
            _ => None,
        }
    }
}

/// What a completed frame turned out to be.
#[derive(Clone, Copy)]
pub enum Framed {
    Event(UsbEvent),
    /// The host asking for the summary that ends a test run.
    Report,
}

// - the journal --------------------------------------------------------------

/// What the consumer saw, for whoever is allowed to print.
///
/// The same argument `src/events.rs` makes: the code that dispatches may not
/// spin on `LSR.THRE`, so it records a few numbers and normal context formats
/// them. Under RTIC the dispatch is a task rather than a handler and the rule is
/// weaker -- a task is preemptible -- but a task that blocks on a console still
/// blocks every lower-priority task, so both binaries do the same thing and stay
/// comparable.
pub struct Journal {
    /// `[event code, endpoint, state index, endpoint writes so far]`.
    data: [[AtomicU8; 4]; 64],
    head: AtomicUsize,
    tail: AtomicUsize,
}

impl Journal {
    pub const fn new() -> Self {
        Journal {
            data: [const { [const { AtomicU8::new(0) }; 4] }; 64],
            head: AtomicUsize::new(0),
            tail: AtomicUsize::new(0),
        }
    }

    pub fn record(&self, event: UsbEvent, state: State, writes: u32) {
        let head = self.head.load(Ordering::Relaxed);
        let next = (head + 1) % 64;
        if next == self.tail.load(Ordering::Acquire) {
            return;
        }
        let endpoint = match event {
            UsbEvent::BusReset => 0,
            UsbEvent::ReceivePacket(ep)
            | UsbEvent::SendComplete(ep)
            | UsbEvent::ReceiveSetupPacket(ep, _) => ep,
        };
        let slot = &self.data[head];
        slot[0].store(event.code(), Ordering::Relaxed);
        slot[1].store(endpoint, Ordering::Relaxed);
        slot[2].store(state.index(), Ordering::Relaxed);
        slot[3].store(writes as u8, Ordering::Relaxed);
        self.head.store(next, Ordering::Release);
    }

    pub fn take(&self) -> Option<[u8; 4]> {
        let tail = self.tail.load(Ordering::Relaxed);
        if tail == self.head.load(Ordering::Acquire) {
            return None;
        }
        let slot = &self.data[tail];
        let record = [
            slot[0].load(Ordering::Relaxed),
            slot[1].load(Ordering::Relaxed),
            slot[2].load(Ordering::Relaxed),
            slot[3].load(Ordering::Relaxed),
        ];
        self.tail.store((tail + 1) % 64, Ordering::Release);
        Some(record)
    }
}

// - the event queue ----------------------------------------------------------

/// Events the handler has produced and the consumer has not taken yet.
///
/// moondancer's is `heapless::mpmc::MpMcQueue<InterruptEvent, 64>`. This is the
/// SPSC ring `src/irq.rs` already ships, widened from a byte to a fixed record,
/// because pulling in `heapless` for one queue is the dependency this crate's
/// Cargo.toml opens by explaining it does not take.
///
/// **Both binaries use this, and that is a finding rather than a shortcut.**
/// RTIC cannot own it: its producer is the interrupt front end, not an RTIC
/// task and has no `lock`. See the module comment in `src/bin/usb_rtic.rs`.
const QUEUE: usize = 64;

/// A queued event, in the wire encoding: code, endpoint, then the setup packet.
const RECORD: usize = 10;

pub struct EventQueue {
    data: [[AtomicU8; RECORD]; QUEUE],
    head: AtomicUsize,
    tail: AtomicUsize,
    /// Events dropped because the queue was full. moondancer prints every
    /// queued event and then spins forever on overflow; this counts, because a
    /// spike that hangs tells a test nothing.
    pub overflows: AtomicU32,
    pub depth_max: AtomicUsize,
}

impl EventQueue {
    pub const fn new() -> Self {
        EventQueue {
            data: [const { [const { AtomicU8::new(0) }; RECORD] }; QUEUE],
            head: AtomicUsize::new(0),
            tail: AtomicUsize::new(0),
            overflows: AtomicU32::new(0),
            depth_max: AtomicUsize::new(0),
        }
    }

    /// The report frame goes through this queue too, and not through a flag of
    /// its own.
    ///
    /// It did have a flag, and that was a race: the handler could set it while
    /// the consumer was past its drain, so the summary printed with the last
    /// event still queued. It read as `dispatched 17` on one run in sixteen.
    /// Two channels with no ordering between them is the bug; one ordered
    /// channel is the fix.
    const REPORT: u8 = 255;

    pub fn enqueue(&self, framed: Framed) {
        let head = self.head.load(Ordering::Relaxed);
        let next = (head + 1) % QUEUE;
        if next == self.tail.load(Ordering::Acquire) {
            self.overflows.fetch_add(1, Ordering::Relaxed);
            return;
        }

        let slot = &self.data[head];
        match framed {
            Framed::Report => slot[0].store(Self::REPORT, Ordering::Relaxed),
            Framed::Event(event) => {
                slot[0].store(event.code(), Ordering::Relaxed);
                match event {
                    UsbEvent::BusReset => slot[1].store(0, Ordering::Relaxed),
                    UsbEvent::ReceivePacket(ep) | UsbEvent::SendComplete(ep) => {
                        slot[1].store(ep, Ordering::Relaxed);
                    }
                    UsbEvent::ReceiveSetupPacket(ep, setup) => {
                        slot[1].store(ep, Ordering::Relaxed);
                        slot[2].store(setup.request_type, Ordering::Relaxed);
                        slot[3].store(setup.request, Ordering::Relaxed);
                        for (i, b) in setup
                            .value
                            .to_le_bytes()
                            .iter()
                            .chain(setup.index.to_le_bytes().iter())
                            .chain(setup.length.to_le_bytes().iter())
                            .enumerate()
                        {
                            slot[4 + i].store(*b, Ordering::Relaxed);
                        }
                    }
                }
            }
        }
        // Release: the record must be visible before the index that publishes it.
        self.head.store(next, Ordering::Release);

        let depth = (next + QUEUE - self.tail.load(Ordering::Relaxed)) % QUEUE;
        if depth > self.depth_max.load(Ordering::Relaxed) {
            self.depth_max.store(depth, Ordering::Relaxed);
        }
    }

    pub fn dequeue(&self) -> Option<Framed> {
        let tail = self.tail.load(Ordering::Relaxed);
        if tail == self.head.load(Ordering::Acquire) {
            return None;
        }
        let slot = &self.data[tail];
        let at = |i: usize| slot[i].load(Ordering::Relaxed);
        let framed = match at(0) {
            10 => Framed::Event(UsbEvent::BusReset),
            12 => Framed::Event(UsbEvent::ReceivePacket(at(1))),
            13 => Framed::Event(UsbEvent::SendComplete(at(1))),
            Self::REPORT => Framed::Report,
            _ => {
                let mut raw = [0_u8; 8];
                for (i, slot) in raw.iter_mut().enumerate() {
                    *slot = at(2 + i);
                }
                Framed::Event(UsbEvent::ReceiveSetupPacket(at(1), SetupPacket::from(raw)))
            }
        };
        self.tail.store((tail + 1) % QUEUE, Ordering::Release);
        Some(framed)
    }
}
