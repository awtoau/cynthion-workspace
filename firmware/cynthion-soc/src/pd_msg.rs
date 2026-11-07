//! USB PD messages: header and object fields, and the FUSB302B FIFO framing.
//!
//! Pure: no `crate::` paths, so `firmware/cynthion-soc-tests` runs it on the host.
//! Field layouts are USB PD R3.1 V1.6 (`~/vendors/usb-if/sources/`): header §6.2.1,
//! Source_Capabilities §6.4.1, Request §6.4.2. The chip adds K-codes, CRC32 and
//! GoodCRC; the FIFO tokens are FUSB302B Rev 1.3 table 29/30 (p. 32-33).

use core::fmt;

pub const MAX_OBJECTS: usize = 7;
/// Header bits 7:6, `01` = revision 2.0: the chip's auto-GoodCRC can only say
/// 1.0 or 2.0 (SWITCHES1.SPECREV), and the two must agree.
const SPEC_REV: u16 = 1;

// Control message types (header bits 4:0, no data objects).
pub const GOOD_CRC: u8 = 1;
pub const ACCEPT: u8 = 3;
pub const REJECT: u8 = 4;
pub const PING: u8 = 5;
pub const PS_RDY: u8 = 6;
pub const GET_SOURCE_CAP: u8 = 7;
pub const SOFT_RESET: u8 = 13;

// Data message types.
pub const SOURCE_CAP: u8 = 1;
pub const REQUEST: u8 = 2;

const CONTROL_NAMES: [&str; 17] = [
    "?0", "GoodCRC", "GotoMin", "Accept", "Reject", "Ping", "PS_RDY", "Get_Source_Cap",
    "Get_Sink_Cap", "DR_Swap", "PR_Swap", "VCONN_Swap", "Wait", "Soft_Reset", "Data_Reset",
    "Data_Reset_Complete", "Not_Supported",
];
const DATA_NAMES: [&str; 8] = [
    "?0", "Source_Capabilities", "Request", "BIST", "Sink_Capabilities", "Battery_Status",
    "Alert", "Get_Country_Info",
];

/// One PD message without its SOP and CRC.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Message {
    pub header: u16,
    pub data: [u32; MAX_OBJECTS],
}

impl Message {
    pub const EMPTY: Message = Message { header: 0, data: [0; MAX_OBJECTS] };

    /// A message from the source (power role source, data role DFP).
    pub fn source(kind: u8, id: u8, objects: &[u32]) -> Message {
        let mut data = [0; MAX_OBJECTS];
        data[..objects.len()].copy_from_slice(objects);
        let header = (kind as u16 & 0x1f)
            | (1 << 5) // data role DFP
            | (SPEC_REV << 6)
            | (1 << 8) // power role source
            | ((id as u16 & 7) << 9)
            | ((objects.len() as u16) << 12);
        Message { header, data }
    }

    pub fn kind(&self) -> u8 {
        (self.header & 0x1f) as u8
    }

    pub fn id(&self) -> u8 {
        ((self.header >> 9) & 7) as u8
    }

    pub fn count(&self) -> usize {
        ((self.header >> 12) & 7) as usize
    }

    pub fn objects(&self) -> &[u32] {
        &self.data[..self.count()]
    }

    /// Control messages have no objects; data messages have at least one.
    pub fn is_control(&self) -> bool {
        self.count() == 0
    }
}

/// FIFO tokens (datasheet table 29).
const SOP1: u8 = 0x12;
const SOP2: u8 = 0x13;
const PACKSYM: u8 = 0x80;
const JAM_CRC: u8 = 0xff;
const EOP: u8 = 0x14;
const TXOFF: u8 = 0xfe;
const TXON: u8 = 0xa1;

/// Longest frame [`encode_tx`] writes: 4 sync + 1 PACKSYM + 2 + 28 + 4 trailer.
pub const TX_MAX: usize = 39;

/// The TX FIFO bytes for one SOP message, ending in TXON, so one I2C write
/// sends it. The chip appends the CRC at JAM_CRC.
pub fn encode_tx(message: &Message, out: &mut [u8; TX_MAX]) -> usize {
    let payload = 2 + 4 * message.count();
    out[..4].copy_from_slice(&[SOP1, SOP1, SOP1, SOP2]);
    out[4] = PACKSYM | payload as u8;
    out[5..7].copy_from_slice(&message.header.to_le_bytes());
    let mut at = 7;
    for object in message.objects() {
        out[at..at + 4].copy_from_slice(&object.to_le_bytes());
        at += 4;
    }
    out[at..at + 4].copy_from_slice(&[JAM_CRC, EOP, TXOFF, TXON]);
    at + 4
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum RxError {
    /// First byte is not an SOP token (SOP' / SOP'' are not enabled).
    NotSop,
    Short,
}

/// RX FIFO packet: token, header (2), objects, CRC (4). The chip drops bad-CRC
/// packets; the CRC bytes are returned in the FIFO and ignored here.
pub fn rx_remaining(first: &[u8; 3]) -> usize {
    4 * (((first[2] >> 4) & 7) as usize) + 4
}

pub fn decode_rx(bytes: &[u8]) -> Result<Message, RxError> {
    if bytes.len() < 3 {
        return Err(RxError::Short);
    }
    if bytes[0] & 0xe0 != 0xe0 {
        return Err(RxError::NotSop);
    }
    let header = u16::from_le_bytes([bytes[1], bytes[2]]);
    let mut message = Message { header, data: [0; MAX_OBJECTS] };
    let count = message.count();
    if bytes.len() < 3 + 4 * count {
        return Err(RxError::Short);
    }
    for index in 0..count {
        let at = 3 + 4 * index;
        message.data[index] =
            u32::from_le_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]]);
    }
    Ok(message)
}

/// Fixed-supply PDO (`mv` in mV, `ma` in mA) with bit 26, USB communications
/// capable, set; every other flag clear.
pub const fn fixed_pdo(mv: u32, ma: u32) -> u32 {
    (1 << 26) | ((mv / 50) << 10) | (ma / 10)
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Pdo {
    Fixed { mv: u32, ma: u32 },
    Battery,
    Variable,
    Pps { min_mv: u32, max_mv: u32, ma: u32 },
    Other,
}

pub fn pdo(object: u32) -> Pdo {
    match object >> 30 {
        0 => Pdo::Fixed { mv: ((object >> 10) & 0x3ff) * 50, ma: (object & 0x3ff) * 10 },
        1 => Pdo::Battery,
        2 => Pdo::Variable,
        _ if (object >> 28) & 3 == 0 => Pdo::Pps {
            min_mv: ((object >> 8) & 0xff) * 100,
            max_mv: ((object >> 17) & 0xff) * 100,
            ma: (object & 0x7f) * 50,
        },
        _ => Pdo::Other,
    }
}

/// A Request data object, read against the capabilities it answers.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Rdo(pub u32);

impl Rdo {
    pub fn position(&self) -> usize {
        ((self.0 >> 28) & 7) as usize
    }
    pub fn mismatch(&self) -> bool {
        self.0 & (1 << 26) != 0
    }
    pub fn usb_comm(&self) -> bool {
        self.0 & (1 << 25) != 0
    }
    pub fn no_suspend(&self) -> bool {
        self.0 & (1 << 24) != 0
    }
    /// Operating and maximum current for a fixed/variable request, in mA.
    pub fn fixed_ma(&self) -> (u32, u32) {
        (((self.0 >> 10) & 0x3ff) * 10, (self.0 & 0x3ff) * 10)
    }
}

/// Is `rdo` something these capabilities can honour? Fixed supplies only: the
/// object must exist and the operating current must not exceed what it offers.
/// A maximum above the offer is the sink's to flag with the mismatch bit.
pub fn request_ok(rdo: Rdo, caps: &[u32]) -> bool {
    let position = rdo.position();
    if position == 0 || position > caps.len() {
        return false;
    }
    match pdo(caps[position - 1]) {
        Pdo::Fixed { ma, .. } => rdo.fixed_ma().0 <= ma,
        _ => false,
    }
}

pub fn kind_name(message: &Message) -> &'static str {
    let index = message.kind() as usize;
    let table: &[&str] = if message.is_control() { &CONTROL_NAMES } else { &DATA_NAMES };
    table.get(index).copied().unwrap_or("?")
}

/// Decoded message plus raw bytes, one line, for the log.
pub struct Decoded<'a> {
    pub message: &'a Message,
    /// The capabilities the sink is answering, to read a Request against.
    pub caps: &'a [u32],
}

impl fmt::Display for Decoded<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let m = self.message;
        write!(f, "{} id{} [{:04x}", kind_name(m), m.id(), m.header)?;
        for object in m.objects() {
            write!(f, " {:08x}", object)?;
        }
        f.write_str("]")?;
        match m.kind() {
            SOURCE_CAP if !m.is_control() => {
                for (index, &object) in m.objects().iter().enumerate() {
                    match pdo(object) {
                        Pdo::Fixed { mv, ma } => write!(f, " {}:{}mV/{}mA", index + 1, mv, ma)?,
                        Pdo::Pps { min_mv, max_mv, ma } => {
                            write!(f, " {}:PPS {}-{}mV/{}mA", index + 1, min_mv, max_mv, ma)?
                        }
                        _ => write!(f, " {}:other", index + 1)?,
                    }
                }
            }
            REQUEST if !m.is_control() => {
                let rdo = Rdo(m.data[0]);
                let offered = rdo.position().checked_sub(1).and_then(|i| self.caps.get(i));
                match offered.map(|&o| pdo(o)) {
                    Some(Pdo::Pps { .. }) => write!(
                        f,
                        " pos{} PPS {}mV {}mA",
                        rdo.position(),
                        ((rdo.0 >> 9) & 0xfff) * 20,
                        (rdo.0 & 0x7f) * 50
                    )?,
                    _ => {
                        let (operating, maximum) = rdo.fixed_ma();
                        write!(f, " pos{} op {}mA max {}mA", rdo.position(), operating, maximum)?
                    }
                }
                write!(
                    f,
                    " mismatch={} usbcomm={} nosuspend={}",
                    rdo.mismatch() as u8,
                    rdo.usb_comm() as u8,
                    rdo.no_suspend() as u8
                )?;
            }
            _ => {}
        }
        Ok(())
    }
}

/// Host tests only; the chip computes and checks the CRC on the board.
#[allow(dead_code)]
pub fn crc32(bytes: &[u8]) -> u32 {
    let mut crc = 0xffff_ffffu32;
    for &byte in bytes {
        crc ^= byte as u32;
        for _ in 0..8 {
            crc = if crc & 1 != 0 { (crc >> 1) ^ 0xedb8_8320 } else { crc >> 1 };
        }
    }
    !crc
}
