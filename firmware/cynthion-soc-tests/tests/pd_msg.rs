//! PD message fields and FIFO framing (#633). Vectors are computed from the
//! field layouts in USB PD R3.1 §6.2.1 / §6.4.1 / §6.4.2 and the FUSB302B
//! token table (datasheet p. 32).

use cynthion_soc_tests::pd_msg::*;

#[test]
fn crc32_is_the_pd_polynomial() {
    // CRC-32/ISO-HDLC check value.
    assert_eq!(crc32(b"123456789"), 0xcbf4_3926);
    // Data followed by its own CRC (LE) always leaves the same residue.
    let mut framed = b"123456789".to_vec();
    framed.extend_from_slice(&crc32(b"123456789").to_le_bytes());
    assert_eq!(crc32(&framed), 0x2144_df1c);
}

#[test]
fn five_volt_three_amp_pdo() {
    assert_eq!(fixed_pdo(5000, 3000), 0x0401_912c);
    assert!(pdo(0x0401_912c) == Pdo::Fixed { mv: 5000, ma: 3000 });
}

#[test]
fn source_cap_header() {
    let m = Message::source(SOURCE_CAP, 0, &[fixed_pdo(5000, 3000)]);
    // type 1, DFP, rev 2.0, source, id 0, one object.
    assert_eq!(m.header, 0x1161);
    assert_eq!(m.count(), 1);
    assert!(!m.is_control());
}

#[test]
fn message_id_wraps_in_three_bits() {
    assert_eq!(Message::source(ACCEPT, 7, &[]).id(), 7);
    assert_eq!(Message::source(ACCEPT, 9, &[]).id(), 1);
    // Accept: type 3, DFP, rev 2.0, source, id 2.
    assert_eq!(Message::source(ACCEPT, 2, &[]).header, 0x0563);
}

#[test]
fn tx_framing_matches_the_token_table() {
    let m = Message::source(SOURCE_CAP, 0, &[fixed_pdo(5000, 3000)]);
    let mut out = [0u8; TX_MAX];
    let n = encode_tx(&m, &mut out);
    assert_eq!(
        &out[..n],
        &[
            0x12, 0x12, 0x12, 0x13, // SOP: Sync-1 x3, Sync-2
            0x80 | 6, // PACKSYM, 2 header + 4 object bytes
            0x61, 0x11, // header 0x1161 LE
            0x2c, 0x91, 0x01, 0x04, // PDO 0x0401912c LE
            0xff, 0x14, 0xfe, 0xa1, // JAM_CRC, EOP, TXOFF, TXON
        ]
    );
}

#[test]
fn control_message_framing() {
    let m = Message::source(GET_SOURCE_CAP, 3, &[]);
    let mut out = [0u8; TX_MAX];
    let n = encode_tx(&m, &mut out);
    assert_eq!(n, 11);
    assert_eq!(out[4], 0x82);
}

#[test]
fn largest_frame_fits() {
    let m = Message::source(SOURCE_CAP, 0, &[0; 7]);
    let mut out = [0u8; TX_MAX];
    assert_eq!(encode_tx(&m, &mut out), TX_MAX);
}

#[test]
fn rx_decode_request() {
    // SOP token, header 0x1082: Request, one object, sink, id 0.
    let rdo = 0x1001_9064u32; // pos 1, 1 A op, 1 A max
    let mut bytes = vec![0xe0, 0x82, 0x10];
    bytes.extend_from_slice(&rdo.to_le_bytes());
    bytes.extend_from_slice(&[0, 0, 0, 0]); // CRC, not checked here
    let first = [bytes[0], bytes[1], bytes[2]];
    assert_eq!(rx_remaining(&first), 8);
    let m = decode_rx(&bytes).unwrap();
    assert_eq!(m.kind(), REQUEST);
    assert_eq!(m.objects(), &[rdo]);
    let r = Rdo(rdo);
    assert_eq!(r.position(), 1);
    assert_eq!(r.fixed_ma(), (1000, 1000));
    assert!(!r.mismatch());
}

#[test]
fn rx_rejects_non_sop_and_short() {
    assert!(decode_rx(&[0xc0, 0, 0]).err() == Some(RxError::NotSop));
    assert!(decode_rx(&[0xe0, 0x82]).err() == Some(RxError::Short));
    assert!(decode_rx(&[0xe0, 0x82, 0x10]).err() == Some(RxError::Short));
}

#[test]
fn request_validation() {
    let caps = [fixed_pdo(5000, 1500)];
    let rdo = |pos: u32, op: u32, max: u32| Rdo((pos << 28) | (op / 10) << 10 | (max / 10));
    assert!(request_ok(rdo(1, 1500, 1500), &caps));
    assert!(request_ok(rdo(1, 900, 3000), &caps)); // max above offer: the mismatch bit's business
    assert!(!request_ok(rdo(1, 2000, 2000), &caps));
    assert!(!request_ok(rdo(0, 100, 100), &caps));
    assert!(!request_ok(rdo(2, 100, 100), &caps));
}

#[test]
fn display_names_and_decodes() {
    use std::fmt::Write;
    let caps = [fixed_pdo(5000, 3000)];
    let m = Message::source(SOURCE_CAP, 0, &caps);
    let mut s = String::new();
    write!(s, "{}", Decoded { message: &m, caps: &caps }).unwrap();
    assert_eq!(s, "Source_Capabilities id0 [1161 0401912c] 1:5000mV/3000mA");
    let r = Message { header: 0x1082, data: [0x1001_9064 | 1 << 26, 0, 0, 0, 0, 0, 0] };
    s.clear();
    write!(s, "{}", Decoded { message: &r, caps: &caps }).unwrap();
    assert!(s.starts_with("Request id0 [1082 14019064] pos1 op 1000mA max 1000mA mismatch=1"), "{s}");
}
