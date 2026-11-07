//! The device interface (#303): framing, every op, the watchdog.
//!
//! Valid frames come from the Python encoder through `ops_golden.txt`; malformed
//! ones are mutations of those, so the firmware is never checked against its own
//! encoder alone. `Fake` returns the values `scripts/soc/soc_ops.py` encodes into
//! the golden responses; `bump` shifts one number per op so each golden
//! comparison is shown able to fail.

use cynthion_soc_tests::ops::dispatch::*;
use cynthion_soc_tests::ops::engine::*;
use cynthion_soc_tests::ops::frame::{self, Parser, Rx};
use cynthion_soc_tests::ops::proto::*;

const GOLDEN: &str = include_str!("ops_golden.txt");
const AT: u32 = 5000;

// ---- helpers ---------------------------------------------------------------

fn unhex(s: &str) -> Vec<u8> {
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// (name, request frame, response frame), from the Python generator.
fn golden() -> Vec<(String, Vec<u8>, Vec<u8>)> {
    GOLDEN
        .lines()
        .filter(|l| !l.starts_with('#') && !l.is_empty())
        .map(|l| {
            let p: Vec<&str> = l.split(" | ").collect();
            (p[0].to_string(), unhex(p[1]), unhex(p[2]))
        })
        .collect()
}

/// Independent CRC-32 (zlib), so frames built here do not use the code under test.
fn crc32(data: &[u8]) -> u32 {
    let mut c = 0xFFFF_FFFFu32;
    for &b in data {
        c ^= b as u32;
        for _ in 0..8 {
            c = (c >> 1) ^ (0xEDB8_8320 & (c & 1).wrapping_neg());
        }
    }
    !c
}

/// A SLIP frame built from raw header fields and body, with a valid CRC.
fn build(kind: u8, verflags: u8, seq: u32, body: &[u8]) -> Vec<u8> {
    let mut p = vec![kind, verflags];
    p.extend((body.len() as u16).to_le_bytes());
    p.extend(seq.to_le_bytes());
    p.extend(0u32.to_le_bytes());
    p.extend(body);
    let crc = crc32(&p);
    p.extend(crc.to_le_bytes());
    slip(&p)
}

fn slip(p: &[u8]) -> Vec<u8> {
    let mut out = vec![0xC0];
    for &b in p {
        match b {
            0xC0 => out.extend([0xDB, 0xDC]),
            0xDB => out.extend([0xDB, 0xDD]),
            _ => out.push(b),
        }
    }
    out.push(0xC0);
    out
}

fn ping(id: u32, nonce: u32) -> Vec<u8> {
    let mut body = OP_SYS_PING.to_le_bytes().to_vec();
    body.extend(nonce.to_le_bytes());
    build(KIND_REQ, VERSION << 4, id, &body)
}

#[derive(Default)]
struct Frames(Vec<Vec<u8>>);

impl Sink for Frames {
    fn frame(&mut self, f: &[u8]) {
        self.0.push(f.to_vec());
    }
}

impl Frames {
    /// Frames as the wire carries them.
    fn wire(&self) -> Vec<Vec<u8>> {
        self.0
            .iter()
            .map(|f| {
                let mut out = Vec::new();
                frame::escape(f, &mut |b| out.push(b));
                out
            })
            .collect()
    }
    fn kinds(&self) -> Vec<u8> {
        self.0.iter().map(|f| f[0]).collect()
    }
    /// (code, value) of an ERROR frame.
    fn error(&self, n: usize) -> (u32, u32) {
        let f = &self.0[n];
        assert_eq!(f[0], KIND_ERROR);
        (
            u32::from_le_bytes(f[12..16].try_into().unwrap()),
            u32::from_le_bytes(f[16..20].try_into().unwrap()),
        )
    }
}

fn identity() -> Identity {
    Identity { fw_git: 0x8000_1A2B, gw_git: 0, time_hz: 60_000_000, session_id: 7 }
}

/// An engine already in a session at `t`, with the SESSION frame consumed.
fn session(d: &mut Fake, t: u32) -> (Engine, Frames) {
    let mut e = Engine::new();
    let mut out = Frames::default();
    e.enter(t, identity(), &mut out);
    assert_eq!(out.kinds(), [KIND_SESSION]);
    out.0.clear();
    let _ = d;
    (e, out)
}

fn feed(e: &mut Engine, d: &mut Fake, out: &mut Frames, t: u32, bytes: &[u8]) -> Step {
    let mut step = Step::Continue;
    for &b in bytes {
        step = e.feed(t, b, d, out);
        if step != Step::Continue {
            break;
        }
    }
    step
}

// ---- the fake driver --------------------------------------------------------

#[derive(Default)]
struct Fake {
    bump: u32,
}

fn cc(ok: bool, vbus: bool, cc1: u8, cc2: u8, source_cc: u8, orientation: u8, serviced: u32) -> CcPort {
    CcPort { ok, device_id: 0x91, vbus, cc1, cc2, source_cc, orientation, serviced }
}

impl Driver for Fake {
    fn features(&self) -> u32 {
        (FEAT_BOARD | FEAT_HYPERRAM) + self.bump
    }
    fn info(&mut self) -> Info {
        Info { fw_git: 0x8000_1A2B, time_hz: 60_000_000, uptime_ms: 123_456 + self.bump, console: 0, consoles: 2 }
    }
    fn power_read(&mut self) -> Result<PowerRead, Fail> {
        let c = |mv, ua, drawing| Channel { mv, ua, floor_ua: 20_000, drawing };
        Ok(PowerRead {
            age_ms: 50,
            channels: [
                c(4994 + self.bump, -12_000, false),
                c(5001, 466_000, true),
                c(0, 0, false),
                c(5000, 90_000, true),
            ],
        })
    }
    fn power_limits(&mut self, channel: u8) -> Result<Limits, Fail> {
        assert!((channel as usize) < CHANNELS, "the dispatcher range-checks");
        Ok(Limits {
            ov_mv: 5500 + self.bump as i32,
            uv_mv: 4000,
            oc_ua: 3_000_000,
            uc_ua: -3_000_000,
            armed: 0b0011,
            fired: 0b0010,
        })
    }
    fn vbus_status(&mut self) -> VbusStatus {
        VbusStatus { raw: 0x84 + self.bump as u8, closed: 0b100, powered: 0b0010 }
    }
    fn vbus_set(&mut self, port: u8, action: VbusAction) -> Result<VbusDone, Fail> {
        match (port, action) {
            (1, VbusAction::On { current_ma: 1500 }) => {
                Ok(VbusDone { source: 0, mv: 5001 + self.bump, flags: 0 })
            }
            (3, VbusAction::Source) => Err(Fail::refused(3, 3980)),
            other => panic!("unexpected vbus_set {other:?}"),
        }
    }
    fn led_get(&mut self) -> Result<Leds, Fail> {
        Ok(Leds { lit: 0b001001 + self.bump as u8, fabric: 0b100000, button: true })
    }
    fn led_set(&mut self, led: u8, state: u8) -> Result<Leds, Fail> {
        assert_eq!((led, state), (3, 1));
        Ok(Leds { lit: 0b001001 + self.bump as u8, fabric: 0b100000, button: false })
    }
    fn typec_status(&mut self) -> Result<TypeC, Fail> {
        Ok(TypeC {
            configured: true,
            lines: 3,
            ports: [
                cc(true, true, 1, 0, 1, 2, 4 + self.bump),
                cc(true, false, CC_UNMEASURED, CC_UNMEASURED, 0, 0, 0),
            ],
        })
    }
    fn pd_status(&mut self) -> PdStatus {
        PdStatus {
            armed: true, cc: 1, running: true, ready: true,
            sent: 12 + self.bump, received: 11, errors: 0, hardresets: 0,
            contract_pos: 1, contract_ma: 900, session_bytes: 1536,
        }
    }
    fn usb_port(&mut self, port: u8) -> Result<UsbPort, Fail> {
        assert_eq!(port, 1);
        Ok(UsbPort {
            sample: true,
            mv: 5001 + self.bump,
            ua: 466_000,
            drawing: true,
            switch: 1,
            cable: Some(cc(true, true, 1, 0, 0, 2, 0)),
        })
    }
    fn hr_status(&mut self) -> Result<HrStatus, Fail> {
        Ok(HrStatus {
            id: 0x1234_5678 + self.bump, ck_khz_req: 180_000, ck_khz_meas: 179_900,
            ready: true, locked: true, calibrated: true, busy: false, hr_busy: false,
            last_op: 4, last_code: 0, errors: 0, state: 0, cal_tap: 10, cal_width: 32, cal_valid: true,
        })
    }
    fn selftest(&mut self) -> Selftest {
        let mut items = [(0u8, 0u8); SELFTEST_MAX_ITEMS];
        for (n, slot) in items.iter_mut().enumerate().take(11) {
            *slot = (n as u8, 0);
        }
        items[10] = (10, 2);
        Selftest { passed: 10 + self.bump as u8, failed: 0, skipped: 1, n: 11, items }
    }
}

// ---- golden: every request through the real engine --------------------------

fn run_golden(bump: u32) -> Vec<(String, Vec<u8>, Vec<u8>)> {
    let mut fake = Fake { bump };
    golden()
        .into_iter()
        .map(|(name, req, rsp)| {
            let (mut e, mut out) = session(&mut fake, 0);
            feed(&mut e, &mut fake, &mut out, AT, &req);
            assert_eq!(out.0.len(), 1, "{name}: one response per request");
            (name, rsp, out.wire().remove(0))
        })
        .collect()
}

#[test]
fn every_golden_request_gets_the_exact_golden_response() {
    let cases = run_golden(0);
    assert_eq!(cases.len(), 17);
    for (name, want, got) in cases {
        assert_eq!(hex(&got), hex(&want), "{name}");
    }
}

#[test]
fn cannot_fail_proof_each_golden_comparison_catches_a_changed_value() {
    // Shift one number per op: if the comparison above were vacuous, these
    // would still match.
    for (name, want, got) in run_golden(1) {
        let driven = !matches!(name.as_str(), "sys.ping" | "unknown op" | "bad args" | "vbus.set refused uv");
        if driven {
            assert_ne!(hex(&got), hex(&want), "{name} did not change with its driver value");
        }
    }
}

#[test]
fn ping_echoes_whatever_it_was_given() {
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, 0);
    feed(&mut e, &mut fake, &mut out, 1, &ping(9, 0x0102_0304));
    let f = &out.0[0];
    assert_eq!((f[0], u32::from_le_bytes(f[4..8].try_into().unwrap())), (KIND_RSP, 9));
    assert_eq!(&f[12..19], &[0x01, 0x00, ST_OK, 0x04, 0x03, 0x02, 0x01]);
}

// ---- every op: wrong lengths and out-of-range arguments --------------------

fn call(op: u16, args: &[u8]) -> Vec<u8> {
    let mut fake = Fake::default();
    let mut req = op.to_le_bytes().to_vec();
    req.extend(args);
    let mut out = [0u8; MAX_RSP_BODY];
    let n = dispatch(&mut fake, &req, &mut out);
    out[..n].to_vec()
}

fn status_of(rsp: &[u8]) -> u8 {
    rsp[2]
}

#[test]
fn every_op_refuses_arguments_of_the_wrong_length() {
    let arg_len: [(u16, usize); 14] = [
        (OP_SYS_PING, 4), (OP_SYS_CAPABILITIES, 2), (OP_SYS_INFO, 0), (OP_POWER_READ, 0),
        (OP_POWER_LIMITS, 1), (OP_VBUS_STATUS, 0), (OP_VBUS_SET, 4), (OP_LED_GET, 0),
        (OP_LED_SET, 2), (OP_TYPEC_STATUS, 0), (OP_PD_STATUS, 0), (OP_USB_PORT, 1),
        (OP_HR_STATUS, 0), (OP_SELFTEST_RUN, 0),
    ];
    assert_eq!(arg_len.map(|(op, _)| op), OPS, "every op is in this table");
    for (op, len) in arg_len {
        for wrong in [len + 1, len.wrapping_sub(1)] {
            if wrong == usize::MAX {
                continue;
            }
            let rsp = call(op, &vec![0u8; wrong]);
            assert_eq!(status_of(&rsp), ST_BAD_ARGS, "op {op:#06x} with {wrong} bytes");
            assert_eq!(rsp.len(), 3 + 5, "failure tail is reason:u8 value:u32");
        }
    }
}

#[test]
fn argument_ranges_are_checked_before_the_driver_sees_them() {
    // Fake asserts on out-of-range values; a pass here means the dispatcher stopped them.
    for (op, args) in [
        (OP_POWER_LIMITS, vec![4u8]),
        (OP_USB_PORT, vec![4]),
        (OP_LED_SET, vec![6, 0]),
        (OP_LED_SET, vec![0, 3]),
        (OP_VBUS_SET, vec![4, 0, 0, 0]),
        (OP_VBUS_SET, vec![1, 4, 0, 0]),
        (OP_VBUS_SET, vec![1, 1, 0x28, 0x0A]),        // 2600 mA is not an Rp level
        (OP_VBUS_SET, vec![0, 1, 0xDC, 0x05]),        // Rp current on target_a
        (OP_VBUS_SET, vec![1, 0, 0xDC, 0x05]),        // Rp current when switching off
        (OP_VBUS_SET, vec![1, 2, 0xDC, 0x05]),        // Rp current when closing a source
    ] {
        assert_eq!(status_of(&call(op, &args)), ST_BAD_ARGS, "{op:#06x} {args:?}");
    }
}

#[test]
fn an_unknown_opcode_is_reported_with_its_own_code_echoed() {
    let rsp = call(0x7FFF, &[]);
    assert_eq!(&rsp[..3], &[0xFF, 0x7F, ST_UNKNOWN_OP]);
    // Not just "not in the table": every table entry has a handler.
    for op in OPS {
        assert_ne!(status_of(&call(op, &[])), ST_UNKNOWN_OP, "{op:#06x}");
    }
}

#[test]
fn a_driver_failure_becomes_status_reason_value() {
    let rsp = call(OP_VBUS_SET, &[3, 2, 0, 0]);
    assert_eq!(rsp, [0x01, 0x02, ST_REFUSED, 3, 0x8C, 0x0F, 0, 0]);
}

#[test]
fn capabilities_pages_and_never_overruns() {
    let page = |first: u16| call(OP_SYS_CAPABILITIES, &first.to_le_bytes());
    let n = |r: &[u8]| (r[14], u16::from_le_bytes([r[12], r[13]]));
    assert_eq!(n(&page(0)), (14, 14));
    assert_eq!(n(&page(13)), (1, 14));
    assert_eq!(n(&page(14)), (0, 14));
    assert_eq!(n(&page(0xFFFF)), (0, 14));
    assert_eq!(page(0).len(), 15 + 28);
    // The opcodes listed are the table, in order.
    let r = page(0);
    let listed: Vec<u16> = r[15..].chunks(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
    assert_eq!(listed, OPS);
}

#[test]
fn the_largest_responses_fit_the_buffer() {
    // selftest with every slot used
    let worst = 3 + 4 + 2 * SELFTEST_MAX_ITEMS;
    assert!(worst <= MAX_RSP_BODY);
    assert!(15 + 2 * CAPS_PAGE <= MAX_RSP_BODY);
    // power.read is the widest fixed result
    assert!(call(OP_POWER_READ, &[]).len() <= MAX_RSP_BODY);
}

// ---- framing ---------------------------------------------------------------

fn parse(bytes: &[u8]) -> Vec<Rx> {
    let mut p = Parser::new();
    bytes.iter().map(|&b| p.push(b)).filter(|r| *r != Rx::Pending).collect()
}

fn first_golden_request() -> Vec<u8> {
    ping(1, 0xA5A5_C0DB)
}

#[test]
fn a_valid_request_is_accepted_with_its_id() {
    assert_eq!(parse(&first_golden_request()), [Rx::Request(1)]);
    // and the same bytes as the Python encoder wrote them
    let (_, req, _) = golden().remove(0);
    assert_eq!(req, first_golden_request(), "independent builder == Python encoder");
}

#[test]
fn a_flipped_byte_is_a_crc_error_not_a_request() {
    let mut f = first_golden_request();
    f[5] ^= 0x40;
    let r = parse(&f);
    assert!(matches!(r.as_slice(), [Rx::Reject(ERR_CRC, _)]), "{r:?}");
}

#[test]
fn a_changed_byte_is_caught_wherever_it_lands() {
    // Cannot-fail proof for the CRC check: every position, every one caught.
    let good = first_golden_request();
    for at in 1..good.len() - 1 {
        if good[at] == 0xDB || good[at] == 0xDC || good[at] == 0xDD {
            continue; // part of an escape pair; covered by the escape tests
        }
        let mut f = good.clone();
        f[at] ^= 0x01;
        if f[at] == 0xC0 {
            continue;
        }
        let r = parse(&f);
        assert!(!r.iter().any(|x| matches!(x, Rx::Request(_))), "byte {at} accepted: {r:?}");
        assert_eq!(r.len(), 1, "byte {at}: exactly one verdict");
    }
}

#[test]
fn truncated_frames_are_rejected_and_the_next_frame_still_decodes() {
    let good = first_golden_request();
    for cut in 2..good.len() - 1 {
        let mut stream = good[..cut].to_vec();
        stream.push(0xC0); // the host died here; the next frame's opening END closes it
        stream.extend(&good);
        let r = parse(&stream);
        assert_eq!(*r.last().unwrap(), Rx::Request(1), "cut at {cut}: {r:?}");
        assert!(r.len() >= 2, "cut at {cut}: the stump is reported");
        assert!(!matches!(r[0], Rx::Request(_)), "cut at {cut}: stump accepted");
    }
}

#[test]
fn bad_escape_is_reported_and_resyncs() {
    let mut stream = vec![0xC0, 0x08, 0xDB, 0x00, 0x01, 0xC0];
    stream.extend(first_golden_request());
    let r = parse(&stream);
    assert!(matches!(r[0], Rx::Reject(ERR_ESCAPE, _)), "{r:?}");
    assert_eq!(*r.last().unwrap(), Rx::Request(1));
    // an END inside an escape sequence
    let r = parse(&[0xC0, 0x08, 0xDB, 0xC0]);
    assert!(matches!(r[0], Rx::Reject(ERR_ESCAPE, _)), "{r:?}");
}

#[test]
fn runt_and_length_mismatch_are_distinct_errors() {
    assert!(matches!(parse(&[0xC0, 0x08, 0x10, 0x00, 0xC0])[0], Rx::Reject(ERR_RUNT, 3)));
    // header claims 10 body bytes, frame carries 6
    let mut p = vec![KIND_REQ, VERSION << 4, 10, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0];
    let crc = crc32(&p);
    p.extend(crc.to_le_bytes());
    let r = parse(&slip(&p));
    assert!(matches!(r[0], Rx::Reject(ERR_LENGTH, 10)), "{r:?}");
    // a length past the protocol maximum
    let mut p = vec![KIND_REQ, VERSION << 4, 0x01, 0x01, 1, 0, 0, 0, 0, 0, 0, 0];
    let crc = crc32(&p);
    p.extend(crc.to_le_bytes());
    assert!(matches!(parse(&slip(&p))[0], Rx::Reject(ERR_LENGTH, 257)));
}

#[test]
fn a_frame_longer_than_the_buffer_is_discarded_to_its_end() {
    let body = vec![0u8; MAX_REQ_BODY + 1];
    let f = build(KIND_REQ, VERSION << 4, 1, &body);
    let mut stream = f.clone();
    stream.extend(first_golden_request());
    let r = parse(&stream);
    assert!(matches!(r[0], Rx::Reject(ERR_OVERFLOW, _)), "{r:?}");
    assert_eq!(*r.last().unwrap(), Rx::Request(1), "and the next frame decodes");
    // far longer, never ending: still one verdict at the END, no buffer overrun
    let mut long = vec![0xC0];
    long.extend(vec![0x11u8; 5000]);
    long.push(0xC0);
    let r = parse(&long);
    assert_eq!(r.len(), 1);
    assert!(matches!(r[0], Rx::Reject(ERR_OVERFLOW, _)));
}

#[test]
fn version_is_checked_after_the_crc_and_kind_after_version() {
    let ok_body = [OP_SYS_PING.to_le_bytes().as_slice(), &[0, 0, 0, 0]].concat();
    // future version, valid CRC
    let r = parse(&build(KIND_REQ, 2 << 4, 1, &ok_body));
    assert!(matches!(r[0], Rx::Reject(ERR_VERSION, 2)), "{r:?}");
    // future version, CORRUPTED: reports corruption, not a firmware from the future
    let mut f = build(KIND_REQ, 2 << 4, 1, &ok_body);
    f[6] ^= 1;
    assert!(matches!(parse(&f)[0], Rx::Reject(ERR_CRC, _)));
    // a response sent to the board is not a request
    let r = parse(&build(KIND_RSP, VERSION << 4, 1, &ok_body));
    assert!(matches!(r[0], Rx::Reject(ERR_KIND, 9)), "{r:?}");
    // a request with no room for an opcode
    let r = parse(&build(KIND_REQ, VERSION << 4, 1, &[0x01]));
    assert!(matches!(r[0], Rx::Reject(ERR_SHORT_REQ, 1)), "{r:?}");
}

#[test]
fn etx_and_query_mean_something_only_between_frames() {
    assert_eq!(parse(&[0x03]), [Rx::Leave]);
    assert_eq!(parse(&[0xC0, 0x03]), [Rx::Leave]);
    assert_eq!(parse(&[0x3F]), [Rx::Query]);
    // 0x03 and '?' inside a frame are data
    let r = parse(&ping(2, 0x3F03_3F03));
    assert_eq!(r, [Rx::Request(2)]);
    // a frame that has started is not interrupted by ETX
    let mut stream = vec![0xC0, 0x08, 0x03, 0xC0];
    stream.extend(first_golden_request());
    let r = parse(&stream);
    assert!(!r.contains(&Rx::Leave), "{r:?}");
}

#[test]
fn delimiters_in_the_body_survive_escaping_both_ways() {
    let r = parse(&ping(0xC0DB_C0DB, 0xDBC0_DBC0));
    assert_eq!(r, [Rx::Request(0xC0DB_C0DB)]);
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, 0);
    feed(&mut e, &mut fake, &mut out, 1, &ping(1, 0xDBC0_DBC0));
    let wire = out.wire().remove(0);
    assert_eq!(wire[1..wire.len() - 1].iter().filter(|&&b| b == 0xC0).count(), 0, "no bare END inside");
    assert_eq!(parse_back(&wire), out.0[0]);
}

fn parse_back(wire: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut esc = false;
    for &b in &wire[1..wire.len() - 1] {
        match (esc, b) {
            (true, 0xDC) => out.push(0xC0),
            (true, _) => out.push(0xDB),
            (false, 0xDB) => {
                esc = true;
                continue;
            }
            (false, b) => out.push(b),
        }
        esc = false;
    }
    out
}

// ---- the session -------------------------------------------------------------

#[test]
fn enter_emits_session_with_resync_and_identity() {
    let mut e = Engine::new();
    let mut out = Frames::default();
    e.enter(40, identity(), &mut out);
    let f = &out.0[0];
    assert_eq!((f[0], f[1]), (KIND_SESSION, (VERSION << 4) | FLAG_RESYNC));
    assert_eq!(u32::from_le_bytes(f[4..8].try_into().unwrap()), 0, "stream seq starts at 0");
    assert_eq!(u32::from_le_bytes(f[8..12].try_into().unwrap()), 40);
    assert_eq!(&f[12..14], &[VERSION, 0]);
    assert_eq!(u32::from_le_bytes(f[16..20].try_into().unwrap()), 0x8000_1A2B);
    assert_eq!(u32::from_le_bytes(f[24..28].try_into().unwrap()), 60_000_000);
    assert_eq!(u32::from_le_bytes(f[28..32].try_into().unwrap()), 7);
}

#[test]
fn responses_do_not_consume_the_stream_counter() {
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, 0); // SESSION was seq 0
    feed(&mut e, &mut fake, &mut out, 1, &ping(77, 1));
    e.text(2, b"hello", &mut out);
    let rsp_seq = u32::from_le_bytes(out.0[0][4..8].try_into().unwrap());
    let text_seq = u32::from_le_bytes(out.0[1][4..8].try_into().unwrap());
    assert_eq!((rsp_seq, text_seq), (77, 1), "RSP echoes the id; TEXT is the next stream seq");
    assert_eq!(out.0[1][1] & 0xF, 0, "RESYNC only on the first record");
}

#[test]
fn long_text_is_split_into_records_that_fit() {
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, 0);
    let line: Vec<u8> = (0..300u32).map(|i| b'a' + (i % 26) as u8).collect();
    e.text(5, &line, &mut out);
    assert_eq!(out.kinds(), [KIND_TEXT; 3]);
    let lens: Vec<usize> = out.0.iter().map(|f| f.len() - 16).collect();
    assert_eq!(lens, [128, 128, 44]);
    let joined: Vec<u8> = out.0.iter().flat_map(|f| f[12..f.len() - 4].to_vec()).collect();
    assert_eq!(joined, line);
}

#[test]
fn text_loss_is_reported_as_drop() {
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, 0);
    e.dropped(9, 3, &mut out);
    let f = &out.0[0];
    assert_eq!(f[0], KIND_DROP);
    assert_eq!(u32::from_le_bytes(f[12..16].try_into().unwrap()), 3);
}

#[test]
fn host_etx_ends_the_session_and_says_so() {
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, 0);
    assert_eq!(feed(&mut e, &mut fake, &mut out, 10, &[0x03]), Step::Left(LEAVE_HOST));
    assert_eq!(out.error(0), (ERR_LEFT, LEAVE_HOST));
}

#[test]
fn query_re_emits_session() {
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, 0);
    feed(&mut e, &mut fake, &mut out, 10, &[0x3F]);
    assert_eq!(out.kinds(), [KIND_SESSION]);
}

#[test]
fn malformed_frames_become_error_records_and_no_response() {
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, 0);
    let mut bad = first_golden_request();
    bad[6] ^= 0x10;
    assert_eq!(feed(&mut e, &mut fake, &mut out, 1, &bad), Step::Continue);
    assert_eq!(out.kinds(), [KIND_ERROR]);
    assert_eq!(out.error(0).0, ERR_CRC);
    // the session is alive: the next good request is answered
    feed(&mut e, &mut fake, &mut out, 2, &first_golden_request());
    assert_eq!(out.kinds(), [KIND_ERROR, KIND_RSP]);
}

// ---- the watchdog ------------------------------------------------------------

#[test]
fn watchdog_is_two_and_a_half_seconds_to_the_millisecond() {
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, 1000);
    assert_eq!(e.tick(1000 + WATCHDOG_MS - 1, &mut out), Step::Continue);
    assert!(out.0.is_empty());
    assert_eq!(e.tick(1000 + WATCHDOG_MS, &mut out), Step::Left(LEAVE_WATCHDOG));
    assert_eq!(out.error(0), (ERR_LEFT, LEAVE_WATCHDOG));
    assert_eq!(WATCHDOG_MS, KEEPALIVE_MS * 5 / 4);
}

#[test]
fn any_received_byte_restarts_the_silence_clock() {
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, 0);
    feed(&mut e, &mut fake, &mut out, 2000, &ping(1, 0));
    assert_eq!(e.tick(2000 + WATCHDOG_MS - 1, &mut out), Step::Continue);
    assert_eq!(e.tick(2000 + WATCHDOG_MS, &mut out), Step::Left(LEAVE_WATCHDOG));
}

#[test]
fn a_stuck_host_mid_frame_is_cut_loose() {
    // Half a request, then silence: the failure `load` could not recover from.
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, 0);
    let half = &first_golden_request()[..10];
    feed(&mut e, &mut fake, &mut out, 100, half);
    assert_eq!(e.tick(100 + WATCHDOG_MS - 1, &mut out), Step::Continue);
    assert_eq!(e.tick(100 + WATCHDOG_MS, &mut out), Step::Left(LEAVE_WATCHDOG));
}

#[test]
fn the_clock_wraps_without_a_false_expiry_or_a_missed_one() {
    let start = u32::MAX - 1000;
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, start);
    assert_eq!(e.tick(start.wrapping_add(WATCHDOG_MS - 1), &mut out), Step::Continue);
    assert_eq!(e.tick(start.wrapping_add(WATCHDOG_MS), &mut out), Step::Left(LEAVE_WATCHDOG));
}

#[test]
fn touch_after_a_long_op_restarts_the_clock() {
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, 0);
    e.touch(2400);
    assert_eq!(e.tick(2400 + WATCHDOG_MS - 1, &mut out), Step::Continue);
}

#[test]
fn a_new_session_starts_clean() {
    let mut fake = Fake::default();
    let (mut e, mut out) = session(&mut fake, 0);
    feed(&mut e, &mut fake, &mut out, 1, &first_golden_request()[..6]);
    assert_eq!(feed(&mut e, &mut fake, &mut out, 2, &[0x03]), Step::Continue, "ETX inside a frame");
    e.enter(10, identity(), &mut out);
    out.0.clear();
    feed(&mut e, &mut fake, &mut out, 11, &first_golden_request());
    assert_eq!(out.kinds(), [KIND_RSP], "the stump from the last session is gone");
}
