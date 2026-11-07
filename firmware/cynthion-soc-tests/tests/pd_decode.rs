//! PD frame decode (#633). Vectors are built from the field layouts in USB PD R3.1 V1.6
//! (6.2.1 header, 6.4.1 PDOs, 6.4.2 RDO, 6.4.4 VDM, 6.5.2 Status) and from the S20 capture
//! recorded in #633 (Request `1042 13025896`).

use cynthion_soc_tests::pd_decode::*;

fn text(frame: &Frame, caps: &[u32]) -> String {
    format!("{}", Decoded { frame, caps })
}

fn from(sop: u8, header: u16, payload: &[u8]) -> Frame {
    let mut bytes = header.to_le_bytes().to_vec();
    bytes.extend_from_slice(payload);
    Frame::from_wire(sop, &bytes).unwrap()
}

#[test]
fn header_fields() {
    // S20 Request: type 2, rev 2.0, id 1, one object.
    let f = from(SOP, 0x1042, &0x13025896u32.to_le_bytes());
    assert_eq!((f.kind(), f.id(), f.objects(), f.rev()), (2, 0, 1, 1));
    assert!(!f.extended() && !f.is_control());
    assert_eq!(f.name(), "Request");
}

#[test]
fn source_header_matches_the_engine_that_worked_on_the_s20() {
    // Source_Capabilities, id 0, one object, PD 2.0: header 0x1161 on the wire.
    assert_eq!(source_header(SOURCE_CAP, false, 1, 0, 1), 0x1161);
    // Accept id 1 was [0363].
    assert_eq!(source_header(ACCEPT, false, 0, 1, 1), 0x0363);
}

#[test]
fn request_decodes_against_the_offer() {
    let caps = [fixed_pdo(5000, 1500, PDO_USB_COMM)];
    let f = from(SOP, 0x1042, &0x13025896u32.to_le_bytes());
    let line = text(&f, &caps);
    assert!(line.contains("pos1 op 1500mA max 1500mA"), "{line}");
    assert!(line.contains("usbcomm=1 nosuspend=1 mismatch=0") || line.contains("mismatch=0 usbcomm=1 nosuspend=1"), "{line}");
}

#[test]
fn source_capabilities_line() {
    let f = from(SOP, 0x1161, &fixed_pdo(5000, 1500, PDO_USB_COMM).to_le_bytes());
    let line = text(&f, &[]);
    assert!(line.starts_with("Source_Capabilities id0"), "{line}");
    assert!(line.contains("1:5000mV/1500mA+COMM"), "{line}");
}

#[test]
fn control_and_extended_names() {
    assert_eq!(from(SOP, 0x0010, &[]).name(), "Not_Supported");
    assert_eq!(from(SOP, 0x0018, &[]).name(), "Get_Revision");
    // Extended, one object word, type 2 = Status.
    assert_eq!(from(SOP, 0x9002, &[0, 0]).name(), "Status");
    assert_eq!(from(SOP, 0x9007, &[0, 0]).name(), "Manufacturer_Info");
}

#[test]
fn status_data_block() {
    // Extended header: chunked, size 7. Temp 25 C, external DC power.
    let ext = 0x8000u16 | 7;
    let mut payload = ext.to_le_bytes().to_vec();
    payload.extend_from_slice(&[25, 0x02, 0, 0, 0x02, 0, 0, 0]);
    let f = from(SOP, 0x9002 | (2 << 12), &payload[..10]);
    let line = text(&f, &[]);
    assert!(line.contains("size=7"), "{line}");
    assert!(line.contains("temp=25C input=02"), "{line}");
}

#[test]
fn manufacturer_info_string() {
    let ext = 0x8000u16 | 14;
    let mut payload = ext.to_le_bytes().to_vec();
    payload.extend_from_slice(&[0xe8, 0x04, 0x34, 0x12]); // VID 04e8, PID 1234
    payload.extend_from_slice(b"Pixel\0");
    let f = from(SOP, 0x9007 | (3 << 12), &payload[..12]);
    let line = text(&f, &[]);
    assert!(line.contains("vid=04e8 pid=1234 \"Pixel\""), "{line}");
}

#[test]
fn discover_identity_ack() {
    // VDM header: SVID ff00, structured, v2.0 (1<<13), ACK, Discover Identity.
    let h = 0xff00_0000u32 | 0x8000 | (1 << 13) | (1 << 6) | 1;
    let id = (1u32 << 31) | (1 << 30) | (2 << 27) | 0x04e8;
    let mut p = Vec::new();
    for o in [h, id, 0u32, 0x1234_5678] {
        p.extend_from_slice(&o.to_le_bytes());
    }
    let f = from(SOP, 0x4f | (4 << 12), &p);
    let line = text(&f, &[]);
    assert!(line.contains("Discover_Identity ACK"), "{line}");
    assert!(line.contains("vdm=2.0"), "{line}");
    assert!(line.contains("host=1 device=1 ufp_type=2"), "{line}");
    assert!(line.contains("vid=04e8"), "{line}");
}

#[test]
fn revision_message() {
    // Revision 3.1, version 1.6.
    let o = (3u32 << 28) | (1 << 24) | (1 << 20) | (6 << 16);
    let f = from(SOP, 0x1000 | 12, &o.to_le_bytes());
    assert!(text(&f, &[]).contains("rev=3.1 ver=1.6"));
}

#[test]
fn wire_round_trip_and_bounds() {
    let f = from(SOP_PRIME, 0x1161, &[1, 2, 3, 4]);
    let mut out = [0u8; 2 + MAX_PAYLOAD];
    let n = f.to_wire(&mut out);
    assert_eq!(&out[..n], &[0x61, 0x11, 1, 2, 3, 4]);
    assert!(Frame::from_wire(SOP, &[0u8; 2 + MAX_PAYLOAD + 1]).is_none());
    assert!(Frame::from_wire(SOP, &[0u8; 1]).is_none());
}
