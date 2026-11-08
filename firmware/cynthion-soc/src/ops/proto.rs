//! Op protocol constants. Pure: no `crate::` paths, so the host tests compile it.
//!
//! Contract: `docs/device-interface.md`. Host copy: `scripts/soc/soc_ops.py`; a
//! pytest compares the opcodes below with its table.

pub const VERSION: u8 = 1;

pub const KIND_SESSION: u8 = 0x00;
pub const KIND_DROP: u8 = 0x01;
pub const KIND_TEXT: u8 = 0x03;
pub const KIND_ERROR: u8 = 0x04;
pub const KIND_REQ: u8 = 0x08;
pub const KIND_RSP: u8 = 0x09;

pub const FLAG_RESYNC: u8 = 0x1;

/// Protocol bound on any body (`docs/binary-protocol.md`).
pub const MAX_BODY: usize = 256;
/// Buffer bounds of this firmware, reported by `sys.capabilities`.
pub const MAX_REQ_BODY: usize = 64;
pub const MAX_RSP_BODY: usize = 128;

pub const OP_SYS_PING: u16 = 0x0001;
pub const OP_SYS_CAPABILITIES: u16 = 0x0002;
pub const OP_SYS_INFO: u16 = 0x0003;
pub const OP_POWER_READ: u16 = 0x0100;
pub const OP_POWER_LIMITS: u16 = 0x0101;
pub const OP_VBUS_STATUS: u16 = 0x0200;
pub const OP_VBUS_SET: u16 = 0x0201;
pub const OP_LED_GET: u16 = 0x0300;
pub const OP_LED_SET: u16 = 0x0301;
pub const OP_TYPEC_STATUS: u16 = 0x0400;
pub const OP_PD_STATUS: u16 = 0x0401;
pub const OP_USB_PORT: u16 = 0x0500;
pub const OP_HR_STATUS: u16 = 0x0600;
pub const OP_SELFTEST_RUN: u16 = 0x0700;

/// Every supported opcode, in table order: what `sys.capabilities` pages over.
pub const OPS: [u16; 14] = [
    OP_SYS_PING, OP_SYS_CAPABILITIES, OP_SYS_INFO, OP_POWER_READ, OP_POWER_LIMITS,
    OP_VBUS_STATUS, OP_VBUS_SET, OP_LED_GET, OP_LED_SET, OP_TYPEC_STATUS,
    OP_PD_STATUS, OP_USB_PORT, OP_HR_STATUS, OP_SELFTEST_RUN,
];

/// Opcodes per `sys.capabilities` page: 15 header bytes + 2 x 48 fit `MAX_RSP_BODY`.
pub const CAPS_PAGE: usize = 48;

pub const ST_OK: u8 = 0;
pub const ST_UNKNOWN_OP: u8 = 1;
pub const ST_BAD_ARGS: u8 = 2;
pub const ST_REFUSED: u8 = 3;
pub const ST_UNAVAILABLE: u8 = 4;
pub const ST_IO: u8 = 5;

pub const ERR_CRC: u32 = 1;
pub const ERR_ESCAPE: u32 = 2;
pub const ERR_RUNT: u32 = 3;
pub const ERR_LENGTH: u32 = 4;
pub const ERR_VERSION: u32 = 5;
pub const ERR_KIND: u32 = 6;
pub const ERR_OVERFLOW: u32 = 7;
pub const ERR_SHORT_REQ: u32 = 8;
pub const ERR_LEFT: u32 = 9;

pub const LEAVE_HOST: u32 = 1;
pub const LEAVE_WATCHDOG: u32 = 2;

pub const FEAT_BOARD: u32 = 1 << 0;
pub const FEAT_HYPERRAM: u32 = 1 << 1;

/// The longest the host may stay silent: its keepalive period (`sys.ping` when
/// idle) x 1.25. Waits for any host byte; on expiry the session ends.
pub const KEEPALIVE_MS: u32 = 2000;
pub const WATCHDOG_MS: u32 = KEEPALIVE_MS * 5 / 4;

pub const CHANNELS: usize = 4;
pub const LEDS: usize = 6;
pub const SELFTEST_MAX_ITEMS: usize = 24;

const _: () = assert!(WATCHDOG_MS == 2500);
const _: () = assert!(15 + 2 * CAPS_PAGE <= MAX_RSP_BODY);
const _: () = assert!(MAX_REQ_BODY <= MAX_BODY && MAX_RSP_BODY <= MAX_BODY);
