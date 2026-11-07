//! USB-PD framing and FUSB302B FIFO coding (datasheet tables 41 and 42).

pub(crate) const HEADER_LEN: usize = 2;
pub(crate) const DATA_OBJECT_LEN: usize = 4;
pub(crate) const MAX_DATA_OBJECTS: usize = 7;
pub(crate) const MAX_PAYLOAD_LEN: usize = MAX_DATA_OBJECTS * DATA_OBJECT_LEN;
pub(crate) const MAX_MESSAGE_LEN: usize = HEADER_LEN + MAX_PAYLOAD_LEN;
pub(crate) const CRC_LEN: usize = 4;
const RX_TOKEN_LEN: usize = 1;
pub(crate) const RX_PREFIX_LEN: usize = RX_TOKEN_LEN + HEADER_LEN + CRC_LEN;
pub(crate) const RX_PAYLOAD_OFFSET: usize = RX_TOKEN_LEN + HEADER_LEN;

/// Only ordinary SOP is supported. SOP'/SOP'' have different receive tokens.
pub(crate) fn is_sop(token: u8) -> bool {
    const RX_TOKEN_MASK: u8 = 0b1110_0000;
    const RX_SOP: u8 = 0b1110_0000;
    token & RX_TOKEN_MASK == RX_SOP
}

/// The little-endian two-byte PD header, excluding FIFO tokens and CRC.
pub(crate) struct Header(u16);

impl Header {
    pub(crate) fn from_bytes(bytes: [u8; HEADER_LEN]) -> Self {
        Self(u16::from_le_bytes(bytes))
    }

    pub(crate) fn payload_len(&self) -> usize {
        const OBJECT_COUNT_SHIFT: u32 = 12;
        const OBJECT_COUNT_MASK: u16 = 0b111;
        usize::from((self.0 >> OBJECT_COUNT_SHIFT) & OBJECT_COUNT_MASK) * DATA_OBJECT_LEN
    }

    pub(crate) fn is_good_crc(&self) -> bool {
        const EXTENDED: u16 = 1 << 15;
        const MESSAGE_TYPE_MASK: u16 = 0b1_1111;
        const GOOD_CRC: u16 = 1;
        self.payload_len() == 0 && self.0 & (EXTENDED | MESSAGE_TYPE_MASK) == GOOD_CRC
    }
}

// TX SOP1/SOP2 are Sync-1/Sync-2 symbols, not the RX SOP'/SOP'' tokens.
const TX_SOP1: u8 = 0x12;
const TX_SOP2: u8 = 0x13;
const TX_SOP: [u8; 4] = [TX_SOP1, TX_SOP1, TX_SOP1, TX_SOP2];
const PACKSYM: u8 = 0x80;
const PACKSYM_LEN: usize = 1;
const JAM_CRC: u8 = 0xff;
const EOP: u8 = 0x14;
const TX_OFF: u8 = 0xfe;
// TXON is a command at the end of the write, not a stored FIFO token.
const TX_ON: u8 = 0xa1;
const TX_SUFFIX: [u8; 4] = [JAM_CRC, EOP, TX_OFF, TX_ON];
const TX_PREFIX_LEN: usize = TX_SOP.len() + PACKSYM_LEN;
const MAX_TX_WRITE_LEN: usize = TX_PREFIX_LEN + MAX_MESSAGE_LEN + TX_SUFFIX.len();

/// One contiguous write containing SOP, PACKSYM, message, CRC/EOP and TXON.
pub(crate) struct TxFrame {
    bytes: [u8; MAX_TX_WRITE_LEN],
    len: usize,
}

impl TxFrame {
    pub(crate) fn new(message: &[u8]) -> Option<Self> {
        // PACKSYM permits a header plus at most seven data objects (2..=30).
        if !(HEADER_LEN..=MAX_MESSAGE_LEN).contains(&message.len()) {
            return None;
        }
        let mut frame = Self {
            bytes: [0; MAX_TX_WRITE_LEN],
            len: TX_PREFIX_LEN + message.len() + TX_SUFFIX.len(),
        };
        frame.bytes[..TX_SOP.len()].copy_from_slice(&TX_SOP);
        frame.bytes[TX_SOP.len()] = PACKSYM | message.len() as u8;
        let payload_end = TX_PREFIX_LEN + message.len();
        frame.bytes[TX_PREFIX_LEN..payload_end].copy_from_slice(message);
        frame.bytes[payload_end..frame.len].copy_from_slice(&TX_SUFFIX);
        Some(frame)
    }

    pub(crate) fn as_bytes(&self) -> &[u8] {
        &self.bytes[..self.len]
    }
}
