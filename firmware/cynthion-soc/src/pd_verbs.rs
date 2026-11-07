//! The named messages `pd target_c send` can put on the wire, and how each is built (#633).
//!
//! Pure: no `crate::` paths, host-tested in `firmware/cynthion-soc-tests`. Types and
//! payloads are USB PD R3.1 V1.6 Tables 6-5/6-6/6-53, 6.4.4 (VDM), 6.5.x (data blocks).
//! The same names are the catalogue keys in `docs/pd/messages.toml`.

use crate::pd_decode::{self as dec, Frame};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Class {
    /// Control, data, extended or VDM message sent with the normal exchange.
    Plain,
    SoftReset,
    HardReset,
    DataReset,
    DrSwap,
    PrSwap,
}

#[derive(Clone, Copy)]
pub struct Verb {
    pub name: &'static str,
    pub class: Class,
    pub sop: u8,
    /// Header type field.
    pub kind: u8,
    pub extended: bool,
    /// Data objects, or for extended messages the 4-byte words incl. extended header.
    pub objects: u8,
    /// Object 0 (and, for extended, the extended header + data block bytes).
    pub payload: &'static [u8],
    /// Replies to collect.
    pub want: u8,
    /// Success is the PHY's GoodCRC; no reply is defined.
    pub no_reply: bool,
    /// Needs `!` or `--force`.
    pub destructive: bool,
    /// A source shall not send it (spec Table 6-76/6-77/6-78); needs `--illegal`.
    pub illegal: bool,
    /// Reply timer named in the result when nothing arrives.
    pub timer: &'static str,
    /// The argument is a 16-bit hex SVID that replaces the VDM header's top half.
    pub arg_svid: bool,
}

/// tSenderResponse 27-33 ms (Table 6-68); x1.25 of the 33 ms maximum.
pub const SENDER_RESPONSE: &str = "SenderResponseTimer 30 ms";
pub const SENDER_RESPONSE_MS: u32 = 40;
/// tVDMSenderResponse 24-30 ms; x1.25 of the 30 ms maximum.
pub const VDM_RESPONSE: &str = "VDMSenderResponseTimer 27 ms";
pub const VDM_RESPONSE_MS: u32 = 38;

const fn control(name: &'static str, kind: u8) -> Verb {
    Verb {
        name,
        class: Class::Plain,
        sop: dec::SOP,
        kind,
        extended: false,
        objects: 0,
        payload: &[],
        want: 1,
        no_reply: false,
        destructive: false,
        illegal: false,
        timer: SENDER_RESPONSE,
        arg_svid: false,
    }
}

const fn data(name: &'static str, kind: u8, payload: &'static [u8]) -> Verb {
    Verb { objects: 1, payload, ..control(name, kind) }
}

/// Extended message with a 2-byte data block: chunked, data size 2.
const fn extended(name: &'static str, kind: u8, block: &'static [u8]) -> Verb {
    Verb { extended: true, objects: 1, payload: block, ..control(name, kind) }
}

const fn vdm(name: &'static str, payload: &'static [u8]) -> Verb {
    Verb { timer: VDM_RESPONSE, ..data(name, dec::VENDOR_DEFINED, payload) }
}

const fn destructive(v: Verb) -> Verb {
    Verb { destructive: true, ..v }
}

const fn illegal(v: Verb) -> Verb {
    Verb { illegal: true, ..v }
}

const fn silent(v: Verb) -> Verb {
    Verb { no_reply: true, want: 0, ..v }
}

const fn prime(v: Verb) -> Verb {
    Verb { sop: dec::SOP_PRIME, ..v }
}

// Extended header (chunked, size) then data block, little-endian.
const GET_MFR: &[u8] = &[0x02, 0x80, 0x00, 0x00]; // size 2: target=port, ref=0
const GET_BATTERY: &[u8] = &[0x01, 0x80, 0x00, 0x00]; // size 1: battery 0
// Structured VDM, SVID ff00 (PD SID), version 2.0, REQ.
const DISCOVER_IDENTITY: &[u8] = &[0x01, 0xa0, 0x00, 0xff];
const DISCOVER_SVIDS: &[u8] = &[0x02, 0xa0, 0x00, 0xff];
const DISCOVER_MODES: &[u8] = &[0x03, 0xa0, 0x00, 0xff];
const ALERT_OPERATING: &[u8] = &[0, 0, 0, 0x10]; // type: operating condition change
const COUNTRY_US: &[u8] = &[0, 0, b'S', b'U']; // "US" in bits 31:16
const BIST_CARRIER: &[u8] = &[0, 0, 0, 0x50]; // mode 0101b
const ENTER_USB2: &[u8] = &[0, 0, 0, 0]; // USB mode 000b = USB 2.0
const SOURCE_INFO: &[u8] = &[0, 0, 0, 0];

pub const VERBS: &[Verb] = &[
    // Queries (a source may send; non-disruptive).
    control("sink-cap", 8),
    control("status", 18),
    control("revision", 24),
    control("sink-cap-ext", 22),
    control("country-codes", 21),
    extended("mfr-info", 6, GET_MFR),
    extended("battery-cap", 3, GET_BATTERY),
    extended("battery-status", 4, GET_BATTERY),
    data("country-info", 7, COUNTRY_US),
    vdm("identity", DISCOVER_IDENTITY),
    prime(vdm("identity-cable", DISCOVER_IDENTITY)),
    vdm("svids", DISCOVER_SVIDS),
    Verb { arg_svid: true, ..vdm("modes", DISCOVER_MODES) },
    // Messages with no defined reply.
    silent(control("ping", 5)),
    silent(data("alert", 6, ALERT_OPERATING)),
    silent(data("source-info", 11, SOURCE_INFO)),
    // Spec: a source shall not send these (Sink or DRP only). Probes of the partner.
    illegal(control("source-cap", 7)),
    illegal(control("source-cap-ext", 17)),
    illegal(control("pps-status", 20)),
    illegal(control("source-info-get", 23)),
    // Disruptive: need `!` or `--force`.
    destructive(control("gotomin", 2)),
    destructive(Verb { want: 2, ..control("vconn-swap", 11) }),
    destructive(data("enter-usb", 8, ENTER_USB2)),
    destructive(silent(data("bist-carrier", 3, BIST_CARRIER))),
    destructive(Verb { class: Class::SoftReset, ..control("soft-reset", 13) }),
    destructive(Verb { class: Class::HardReset, ..silent(control("hard-reset", 0)) }),
    destructive(Verb { class: Class::DataReset, ..control("data-reset", 14) }),
    destructive(Verb { class: Class::DrSwap, ..control("dr-swap", 9) }),
    destructive(Verb { class: Class::PrSwap, ..control("pr-swap", 10) }),
];

pub fn find(name: &str) -> Option<&'static Verb> {
    VERBS.iter().find(|v| v.name == name)
}

/// The frame for `verb`. ID, roles and revision are filled in by the engine.
pub fn build(verb: &Verb, svid: Option<u16>) -> Frame {
    let mut f = Frame::EMPTY;
    f.sop = verb.sop;
    f.header = (verb.kind as u16 & 0x1f)
        | ((verb.objects as u16 & 7) << 12)
        | if verb.extended { 0x8000 } else { 0 };
    f.len = verb.payload.len() as u8;
    f.payload[..verb.payload.len()].copy_from_slice(verb.payload);
    if let (true, Some(svid)) = (verb.arg_svid, svid) {
        f.payload[2..4].copy_from_slice(&svid.to_le_bytes());
    }
    f
}

/// Per-verb reply budget and the timer it stands for.
pub fn timeout_ms(verb: &Verb) -> u32 {
    if verb.timer == VDM_RESPONSE {
        VDM_RESPONSE_MS
    } else if verb.name == "vconn-swap" {
        // Accept within tSenderResponse, then PS_RDY within tVCONNSourceOn 50 ms.
        SENDER_RESPONSE_MS + 65
    } else {
        SENDER_RESPONSE_MS
    }
}

/// Parse the hex of `raw <hex>`: header then payload, no separators.
pub fn parse_hex(text: &[u8], out: &mut [u8]) -> Option<usize> {
    if text.len() % 2 != 0 || text.len() / 2 > out.len() {
        return None;
    }
    let nibble = |c: u8| (c as char).to_digit(16).map(|d| d as u8);
    for (i, pair) in text.chunks(2).enumerate() {
        out[i] = nibble(pair[0])? << 4 | nibble(pair[1])?;
    }
    Some(text.len() / 2)
}
