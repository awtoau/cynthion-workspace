//! USB PD frames: header fields, message names, and one-line decodes of every object type
//! a port partner can answer with (#633).
//!
//! Pure: no `crate::` paths, so `firmware/cynthion-soc-tests` runs it on the host.
//! Layouts are USB PD R3.1 V1.6 (`~/vendors/usb-if/sources/`): header 6.2.1, message
//! types Tables 6-5/6-6/6-53, PDOs 6.4.1, RDO 6.4.2, VDM 6.4.4, SDB 6.5.2.

use core::fmt;

pub const MAX_PAYLOAD: usize = 28;

pub const SOP: u8 = 0;
pub const SOP_PRIME: u8 = 1;
pub const SOP_DOUBLE_PRIME: u8 = 2;

pub fn sop_name(sop: u8) -> &'static str {
    match sop {
        SOP => "SOP",
        SOP_PRIME => "SOP'",
        _ => "SOP''",
    }
}

// Control message types (Table 6-5).
pub const GOOD_CRC: u8 = 1;
pub const ACCEPT: u8 = 3;
pub const REJECT: u8 = 4;
pub const WAIT: u8 = 12;
pub const NOT_SUPPORTED: u8 = 16;
// Data message types (Table 6-6).
pub const SOURCE_CAP: u8 = 1;
pub const REQUEST: u8 = 2;
pub const SINK_CAP: u8 = 4;
pub const VENDOR_DEFINED: u8 = 15;

const CONTROL: [&str; 25] = [
    "?0", "GoodCRC", "GotoMin", "Accept", "Reject", "Ping", "PS_RDY", "Get_Source_Cap",
    "Get_Sink_Cap", "DR_Swap", "PR_Swap", "VCONN_Swap", "Wait", "Soft_Reset", "Data_Reset",
    "Data_Reset_Complete", "Not_Supported", "Get_Source_Cap_Extended", "Get_Status", "FR_Swap",
    "Get_PPS_Status", "Get_Country_Codes", "Get_Sink_Cap_Extended", "Get_Source_Info",
    "Get_Revision",
];
const DATA: [&str; 16] = [
    "?0", "Source_Capabilities", "Request", "BIST", "Sink_Capabilities", "Battery_Status",
    "Alert", "Get_Country_Info", "Enter_USB", "EPR_Request", "EPR_Mode", "Source_Info",
    "Revision", "?13", "?14", "Vendor_Defined",
];
const EXTENDED: [&str; 31] = [
    "?0", "Source_Capabilities_Extended", "Status", "Get_Battery_Cap", "Get_Battery_Status",
    "Battery_Capabilities", "Get_Manufacturer_Info", "Manufacturer_Info", "Security_Request",
    "Security_Response", "Firmware_Update_Request", "Firmware_Update_Response", "PPS_Status",
    "Country_Info", "Country_Codes", "Sink_Capabilities_Extended", "Extended_Control",
    "EPR_Source_Capabilities", "EPR_Sink_Capabilities", "?19", "?20", "?21", "?22", "?23", "?24",
    "?25", "?26", "?27", "?28", "?29", "Vendor_Defined_Extended",
];

/// One PD message as the PHY delivers it: header and payload, no token or CRC.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Frame {
    pub sop: u8,
    pub header: u16,
    /// Payload bytes: data objects, or extended header plus data block.
    pub len: u8,
    pub payload: [u8; MAX_PAYLOAD],
}

impl Frame {
    pub const EMPTY: Frame = Frame { sop: 0, header: 0, len: 0, payload: [0; MAX_PAYLOAD] };

    /// From `header (2) + payload`, as `usbpd_traits::Driver::receive` returns it.
    pub fn from_wire(sop: u8, bytes: &[u8]) -> Option<Frame> {
        if bytes.len() < 2 || bytes.len() - 2 > MAX_PAYLOAD {
            return None;
        }
        let mut frame = Frame::EMPTY;
        frame.sop = sop;
        frame.header = u16::from_le_bytes([bytes[0], bytes[1]]);
        frame.len = (bytes.len() - 2) as u8;
        frame.payload[..bytes.len() - 2].copy_from_slice(&bytes[2..]);
        Some(frame)
    }

    /// Header and payload, little-endian, into `out`; returns the length.
    pub fn to_wire(&self, out: &mut [u8; 2 + MAX_PAYLOAD]) -> usize {
        out[..2].copy_from_slice(&self.header.to_le_bytes());
        out[2..2 + self.len as usize].copy_from_slice(self.payload());
        2 + self.len as usize
    }

    pub fn payload(&self) -> &[u8] {
        &self.payload[..self.len as usize]
    }

    pub fn kind(&self) -> u8 {
        (self.header & 0x1f) as u8
    }
    pub fn id(&self) -> u8 {
        ((self.header >> 9) & 7) as u8
    }
    pub fn objects(&self) -> usize {
        ((self.header >> 12) & 7) as usize
    }
    pub fn extended(&self) -> bool {
        self.header & 0x8000 != 0
    }
    /// Header SpecRev: 0 = 1.0, 1 = 2.0, 2 = 3.x.
    pub fn rev(&self) -> u8 {
        ((self.header >> 6) & 3) as u8
    }
    pub fn is_control(&self) -> bool {
        !self.extended() && self.objects() == 0
    }
    pub fn is(&self, kind: u8) -> bool {
        self.is_control() && self.kind() == kind
    }
    /// Data object `index`, or 0 past the end.
    pub fn object(&self, index: usize) -> u32 {
        let at = 4 * index;
        if at + 4 > self.len as usize {
            return 0;
        }
        u32::from_le_bytes([
            self.payload[at],
            self.payload[at + 1],
            self.payload[at + 2],
            self.payload[at + 3],
        ])
    }
    /// Extended header (chunked, chunk number, request-chunk, data size).
    pub fn ext_header(&self) -> Option<(bool, u8, bool, u16)> {
        if !self.extended() || self.len < 2 {
            return None;
        }
        let h = u16::from_le_bytes([self.payload[0], self.payload[1]]);
        Some((h & 0x8000 != 0, ((h >> 11) & 0xf) as u8, h & 0x400 != 0, h & 0x1ff))
    }

    pub fn name(&self) -> &'static str {
        let table: &[&str] = if self.extended() {
            &EXTENDED
        } else if self.objects() == 0 {
            &CONTROL
        } else {
            &DATA
        };
        table.get(self.kind() as usize).copied().unwrap_or("?")
    }
}

/// A header for a message we send: power role source, data role DFP.
pub fn source_header(kind: u8, extended: bool, objects: usize, id: u8, rev: u8) -> u16 {
    (kind as u16 & 0x1f)
        | (1 << 5)
        | ((rev as u16 & 3) << 6)
        | (1 << 8)
        | ((id as u16 & 7) << 9)
        | ((objects as u16 & 7) << 12)
        | if extended { 0x8000 } else { 0 }
}

pub fn fixed_pdo(mv: u32, ma: u32, flags: u32) -> u32 {
    flags | ((mv / 50) << 10) | (ma / 10)
}

/// Fixed-supply PDO flag bit 26: USB communications capable.
pub const PDO_USB_COMM: u32 = 1 << 26;

fn pdo(f: &mut fmt::Formatter<'_>, index: usize, o: u32, sink: bool) -> fmt::Result {
    write!(f, " {}:", index + 1)?;
    match o >> 30 {
        0 => {
            let (mv, ma) = (((o >> 10) & 0x3ff) * 50, (o & 0x3ff) * 10);
            write!(f, "{}mV/{}mA", mv, ma)?;
            // Source flags are bits 29:20, sink flags differ in 28:25 only.
            let bits = [(29, "DRP"), (28, if sink { "HC" } else { "SUSP" }), (27, "UNCON"),
                (26, "COMM"), (25, "DRD"), (24, "UNCHUNK"), (23, "EPR")];
            for (bit, name) in bits {
                if o & (1 << bit) != 0 {
                    write!(f, "+{}", name)?;
                }
            }
        }
        1 => write!(f, "BAT {}-{}mV/{}mW", ((o >> 10) & 0x3ff) * 50, ((o >> 20) & 0x3ff) * 50,
            (o & 0x3ff) * 250)?,
        2 => write!(f, "VAR {}-{}mV/{}mA", ((o >> 10) & 0x3ff) * 50, ((o >> 20) & 0x3ff) * 50,
            (o & 0x3ff) * 10)?,
        _ => match (o >> 28) & 3 {
            0 => write!(f, "PPS {}-{}mV/{}mA", ((o >> 8) & 0xff) * 100, ((o >> 17) & 0xff) * 100,
                (o & 0x7f) * 50)?,
            1 => write!(f, "EPR-AVS {}-{}mV/{}W", ((o >> 8) & 0xff) * 100,
                ((o >> 17) & 0x1ff) * 100, o & 0xff)?,
            _ => write!(f, "APDO {:08x}", o)?,
        },
    }
    Ok(())
}

fn rdo(f: &mut fmt::Formatter<'_>, o: u32, offered: Option<u32>) -> fmt::Result {
    let position = (o >> 28) & 7;
    match offered.map(|p| p >> 30) {
        Some(3) => write!(f, " pos{} PPS {}mV {}mA", position, ((o >> 9) & 0xfff) * 20,
            (o & 0x7f) * 50)?,
        _ => write!(f, " pos{} op {}mA max {}mA", position, ((o >> 10) & 0x3ff) * 10,
            (o & 0x3ff) * 10)?,
    }
    write!(f, " giveback={} mismatch={} usbcomm={} nosuspend={}", (o >> 27) & 1, (o >> 26) & 1,
        (o >> 25) & 1, (o >> 24) & 1)
}

fn vdm(f: &mut fmt::Formatter<'_>, frame: &Frame) -> fmt::Result {
    let h = frame.object(0);
    let svid = h >> 16;
    if h & 0x8000 == 0 {
        return write!(f, " unstructured svid={:04x} payload={:04x}", svid, h & 0x7fff);
    }
    let command = match h & 0x1f {
        1 => "Discover_Identity",
        2 => "Discover_SVIDs",
        3 => "Discover_Modes",
        4 => "Enter_Mode",
        5 => "Exit_Mode",
        6 => "Attention",
        _ => "SVID_specific",
    };
    let kind = ["REQ", "ACK", "NAK", "BUSY"][((h >> 6) & 3) as usize];
    write!(f, " svid={:04x} {} {} objpos={} vdm={}.{}", svid, command, kind, (h >> 8) & 7,
        (h >> 13) & 3, (h >> 11) & 3)?;
    if (h & 0x1f) == 1 && ((h >> 6) & 3) == 1 && frame.objects() >= 2 {
        let id = frame.object(1);
        write!(f, " | id_header={:08x} host={} device={} ufp_type={} dfp_type={} modal={} vid={:04x}",
            id, id >> 31, (id >> 30) & 1, (id >> 27) & 7, (id >> 23) & 7, (id >> 26) & 1,
            id & 0xffff)?;
        if frame.objects() >= 3 {
            write!(f, " xid={:08x}", frame.object(2))?;
        }
        for index in 3..frame.objects() {
            write!(f, " vdo{}={:08x}", index - 3, frame.object(index))?;
        }
    } else if (h & 0x1f) == 2 && ((h >> 6) & 3) == 1 {
        for index in 1..frame.objects() {
            let o = frame.object(index);
            write!(f, " svid={:04x} svid={:04x}", o >> 16, o & 0xffff)?;
        }
    } else {
        for index in 1..frame.objects() {
            write!(f, " vdo{}={:08x}", index, frame.object(index))?;
        }
    }
    Ok(())
}

fn extended_data(f: &mut fmt::Formatter<'_>, frame: &Frame) -> fmt::Result {
    let Some((chunked, chunk, request, size)) = frame.ext_header() else {
        return Ok(());
    };
    write!(f, " ext(chunked={} chunk={} req={} size={})", chunked as u8, chunk, request as u8, size)?;
    let d = &frame.payload()[2..];
    let b = |i: usize| d.get(i).copied().unwrap_or(0);
    let w = |i: usize| u16::from_le_bytes([b(i), b(i + 1)]);
    match frame.kind() {
        2 => write!(f, " temp={}C input={:02x} batt={:02x} events={:02x} tempst={:02x} power={:02x} \
            state={:02x}", b(0), b(1), b(2), b(3), b(4), b(5), b(6)),
        5 => write!(f, " vid={:04x} pid={:04x} design={}00mWh lastfull={}00mWh type={:02x}",
            w(0), w(2), w(4), w(6), b(8)),
        7 => {
            write!(f, " vid={:04x} pid={:04x} \"", w(0), w(2))?;
            for &c in d.iter().skip(4).take_while(|&&c| c != 0) {
                f.write_char(if (0x20..0x7f).contains(&c) { c as char } else { '.' })?;
            }
            f.write_str("\"")
        }
        12 => write!(f, " vout={}mV iout={}mA flags={:02x}", w(0) as u32 * 20, b(2) as u32 * 50, b(3)),
        14 => {
            write!(f, " countries")?;
            for i in (2..d.len()).step_by(2) {
                write!(f, " {}{}", b(i) as char, b(i + 1) as char)?;
            }
            Ok(())
        }
        1 => write!(f, " vid={:04x} pid={:04x} xid={:02x}{:02x}{:02x}{:02x} fw={} hw={} regulation={:02x} \
            holdup={}ms compliance={:02x} touch={:02x} peak=[{:04x} {:04x} {:04x}] touchtemp={} \
            sourceinputs={:02x} batteries={:02x} pdp={}W", w(0), w(2), b(7), b(6), b(5), b(4), b(8),
            b(9), b(10), b(11), b(12), b(13), w(14), w(16), w(18), b(20), b(21), b(22), b(23)),
        15 => write!(f, " vid={:04x} pid={:04x} xid={:02x}{:02x}{:02x}{:02x} fw={} hw={} skedb={} \
            load={:02x} compliance={:02x} touchtemp={} battery={:02x} modes={:02x} minpdp={}W \
            opspdp={}W maxpdp={}W", w(0), w(2), b(7), b(6), b(5), b(4), b(8), b(9), b(10), b(11),
            b(12), b(13), b(14), b(15), b(16), b(17), b(18)),
        _ => Ok(()),
    }
}

use core::fmt::Write as _;

/// A frame plus the capabilities a Request answers, as one log line.
pub struct Decoded<'a> {
    pub frame: &'a Frame,
    pub caps: &'a [u32],
}

impl fmt::Display for Decoded<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let m = self.frame;
        write!(f, "{} id{} rev{} [{:04x}", m.name(), m.id(), ["1.0", "2.0", "3.x", "?"][m.rev() as usize],
            m.header)?;
        for byte in m.payload() {
            write!(f, " {:02x}", byte)?;
        }
        f.write_str("]")?;
        if m.sop != SOP {
            write!(f, " {}", sop_name(m.sop))?;
        }
        if m.extended() {
            return extended_data(f, m);
        }
        match (m.objects(), m.kind()) {
            (0, _) => Ok(()),
            (n, SOURCE_CAP) | (n, SINK_CAP) => {
                for index in 0..n {
                    pdo(f, index, m.object(index), m.kind() == SINK_CAP)?;
                }
                Ok(())
            }
            (_, REQUEST) => {
                let o = m.object(0);
                let offered = (((o >> 28) & 7) as usize).checked_sub(1).and_then(|i| self.caps.get(i));
                rdo(f, o, offered.copied())
            }
            (_, VENDOR_DEFINED) => vdm(f, m),
            (_, 5) => {
                let o = m.object(0);
                write!(f, " present={}00mWh info={:02x}", o >> 16, (o >> 8) & 0xff)
            }
            (_, 6) => {
                let o = m.object(0);
                write!(f, " alert type={:02x} batteries={:02x} extended={:02x}", o >> 24,
                    (o >> 16) & 0xff, o & 0xff)
            }
            (_, 12) => {
                let o = m.object(0);
                write!(f, " rev={}.{} ver={}.{}", o >> 28, (o >> 24) & 0xf, (o >> 20) & 0xf,
                    (o >> 16) & 0xf)
            }
            (_, 10) => {
                let o = m.object(0);
                write!(f, " eprmode action={} data={:02x}", o >> 24, (o >> 16) & 0xff)
            }
            (_, 11) => {
                let o = m.object(0);
                write!(f, " port={} max_pdp={}W present_pdp={}W reported_pdp={}W", o >> 31,
                    (o >> 16) & 0xff, (o >> 8) & 0xff, o & 0xff)
            }
            (_, 3) => write!(f, " bist mode={}", m.object(0) >> 28),
            (n, _) => {
                for index in 0..n {
                    write!(f, " {:08x}", m.object(index))?;
                }
                Ok(())
            }
        }
    }
}
