//! HyperRAM on the #588 peripheral: the 4 KiB window, COPY, and the staging header.
//!
//! - Register map, codes and bounds: `docs/chips/hyperram/soc-peripheral.md`.
//! - Staging (bytes): magic @0, length @4, CRC32 @8, image from @32. Written by
//!   `load` and the JTAG stager, read back by `firmware/cynthion-boot`.
//! - Compiled into `cynthion-boot` by `#[path]`: one layout, one CRC, no statics.

/// Identifies a staged image. Not 0 or all-ones: what blank and failed reads return.
pub const MAGIC: u32 = 0x4359_4e42; // "CYNB"

const HDR_MAGIC: u32 = 0;
const HDR_LENGTH: u32 = 4;
const HDR_CRC: u32 = 8;

/// First byte of the image, leaving room for the header to grow.
pub const IMAGE_BYTE: u32 = 32;

/// Largest image: the image region of block RAM. `soc_generate_pac.py --check` holds it.
pub const MAX_IMAGE: u32 = 31 * 1024;

/// The window, and the size of one COPY chunk.
pub const WINDOW: u32 = 4096;

/// CRC-32 (IEEE 802.3), bitwise: a table costs more block RAM than the check is worth.
pub struct Crc32(u32);

impl Crc32 {
    pub fn new() -> Self {
        Crc32(0xffff_ffff)
    }

    pub fn push(&mut self, byte: u8) {
        self.0 ^= byte as u32;
        for _ in 0..8 {
            let mask = (self.0 & 1).wrapping_neg();
            self.0 = (self.0 >> 1) ^ (0xedb8_8320 & mask);
        }
    }

    pub fn finish(&self) -> u32 {
        !self.0
    }
}

/// Why there is nothing to boot. A record for the boot status; no branch depends on it.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Reject {
    /// No magic: nothing staged, or `--clear` removed it.
    NoMagic,
    /// Magic present, length zero or past the image region.
    Length,
    /// The peripheral did not answer, or refused the read (no calibration).
    Silent,
}

/// One little-endian word at a byte address, via the window. `None`: the op failed.
pub fn read_u32(addr: u32) -> Option<u32> {
    if !backend::copy(backend::OP_COPY_IN, addr, 0, 4) {
        return None;
    }
    Some(backend::win_word0())
}

/// One little-endian word at a byte address, masked: neighbouring bytes are untouched.
pub fn write_u32(addr: u32, value: u32) -> bool {
    backend::win_set_word0(value);
    backend::copy(backend::OP_COPY_OUT, addr, 0, 4)
}

/// Sequential bytes out of HyperRAM, one COPY_IN per window's worth.
pub struct Reader {
    at: u32,
    left_in_window: u32,
    pos: u32,
    pub failed: bool,
}

impl Reader {
    pub fn new(addr: u32) -> Self {
        Reader { at: addr, left_in_window: 0, pos: 0, failed: false }
    }

    /// The next byte; a failed COPY sets `failed` (the bytes are then stale).
    pub fn byte(&mut self) -> u8 {
        if self.left_in_window == 0 {
            if !backend::copy(backend::OP_COPY_IN, self.at, 0, WINDOW) {
                self.failed = true;
            }
            self.left_in_window = WINDOW;
            self.pos = 0;
        }
        let byte = backend::win_get(self.pos);
        self.pos += 1;
        self.left_in_window -= 1;
        self.at += 1;
        byte
    }
}

/// Sequential bytes into HyperRAM, one COPY_OUT per window's worth.
pub struct Writer {
    at: u32,
    fill: u32,
    pub failed: bool,
}

impl Writer {
    pub fn new(addr: u32) -> Self {
        Writer { at: addr, fill: 0, failed: false }
    }

    pub fn byte(&mut self, value: u8) {
        backend::win_put(self.fill, value);
        self.fill += 1;
        if self.fill == WINDOW {
            self.flush();
        }
    }

    pub fn flush(&mut self) {
        if self.fill != 0 && !backend::copy(backend::OP_COPY_OUT, self.at, 0, self.fill) {
            self.failed = true;
        }
        self.at += self.fill;
        self.fill = 0;
    }
}

/// Record a staged image. Magic LAST, so a reset mid-update cannot boot a half header.
pub fn write_header(length: u32, crc: u32) -> bool {
    write_u32(HDR_LENGTH, length) & write_u32(HDR_CRC, crc) & write_u32(HDR_MAGIC, MAGIC)
}

/// Clear the magic so a rejected image is not retried on every boot.
pub fn invalidate() -> bool {
    write_u32(HDR_MAGIC, 0)
}

/// A staged image's length and CRC, or why there is not one.
pub fn staged() -> Result<(u32, u32), Reject> {
    let magic = read_u32(HDR_MAGIC).ok_or(Reject::Silent)?;
    if magic != MAGIC {
        return Err(Reject::NoMagic);
    }
    let length = read_u32(HDR_LENGTH).ok_or(Reject::Silent)?;
    if length == 0 || length > MAX_IMAGE {
        return Err(Reject::Length);
    }
    Ok((length, read_u32(HDR_CRC).ok_or(Reject::Silent)?))
}

/// The peripheral, per `gateware/soc/peripherals/hyperram.py`.
#[cfg(not(feature = "qemu"))]
pub mod backend {
    use core::ptr::{read_volatile, write_volatile};
    use cynthion_soc_pac::hyperram::offset;

    const BASE: usize = cynthion_soc_pac::base::HYPERRAM;
    const WIN: usize = cynthion_soc_pac::base::HYPERRAM_WINDOW;
    const SYNC_HZ: u32 = cynthion_soc_pac::base::SYNC_HZ;
    /// hr cycles per `rdtime` tick, rounded DOWN so a bound never comes out short
    /// (3 at CK 180, 2 at 120; 2 at 165, which is 37% loose).
    const HR_PER_TICK: u32 = {
        let k = cynthion_soc_pac::base::HYPERRAM_CK_HZ / SYNC_HZ;
        if k == 0 { 1 } else { k }
    };

    pub const OP_COPY_OUT: u8 = 1;
    pub const OP_COPY_IN: u8 = 2;
    pub const OP_FILL: u8 = 3;
    pub const OP_TEST: u8 = 4;
    pub const OP_CALIBRATE: u8 = 5;
    pub const OP_REG_READ: u8 = 6;
    pub const OP_REG_WRITE: u8 = 7;
    pub const OP_SET_DELAY: u8 = 8;
    pub const OP_DEVICE_RESET: u8 = 9;
    /// Not a gateware code: the poll ran out first.
    pub const CODE_TIMEOUT: u8 = 0xff;

    pub const STATUS_BUSY: u16 = 1 << 0;
    pub const STATUS_READY: u16 = 1 << 1;
    pub const STATUS_LOCKED: u16 = 1 << 2;
    pub const STATUS_CAL: u16 = 1 << 3;
    pub const STATUS_HR_BUSY: u16 = 1 << 5;

    pub fn w8(off: usize, value: u8) {
        // SAFETY: a byte inside the generated HYPERRAM CSR window.
        unsafe { write_volatile((BASE + off) as *mut u8, value) }
    }

    pub fn r8(off: usize) -> u8 {
        // SAFETY: as `w8`.
        unsafe { read_volatile((BASE + off) as *const u8) }
    }

    /// A multi-byte CSR, low byte first: amaranth-soc commits on the footprint's last
    /// byte, and a register's footprint is a power of two (a 23-bit one is 4 bytes).
    pub fn w32(off: usize, value: u32) {
        for i in 0..4 {
            w8(off + i, (value >> (8 * i)) as u8);
        }
    }

    /// Low byte first: reading it latches the shadow the others come from.
    pub fn r32(off: usize) -> u32 {
        (0..4).fold(0, |acc, i| acc | (r8(off + i) as u32) << (8 * i))
    }

    pub fn r16(off: usize) -> u16 {
        r8(off) as u16 | (r8(off + 1) as u16) << 8
    }

    pub fn w16(off: usize, value: u16) {
        w8(off, value as u8);
        w8(off + 1, (value >> 8) as u8);
    }

    pub fn status() -> u16 {
        r16(offset::STATUS)
    }

    /// Calibration result: (tap, width, valid).
    pub fn calibration() -> (u8, u8, bool) {
        let cal = r32(offset::CAL);
        ((cal & 0x7f) as u8, ((cal >> 8) & 0xff) as u8, (cal >> 16) & 1 == 1)
    }

    /// `hr status`, decoded: the shell renders it, the op protocol encodes it.
    pub struct Snapshot {
        pub id: u32,
        pub ck_khz_req: u32,
        pub ck_khz_meas: u32,
        pub ready: bool,
        pub locked: bool,
        pub calibrated: bool,
        pub busy: bool,
        pub hr_busy: bool,
        pub last_op: u8,
        pub last_code: u8,
        pub errors: u16,
        pub state: u16,
        pub cal_tap: u8,
        pub cal_width: u8,
        pub cal_valid: bool,
    }

    pub fn snapshot() -> Snapshot {
        let s = status();
        // CK_COUNT counts hr cycles over 2^19 cycles of the 60 MHz oscillator.
        let khz = ((r32(offset::CK_COUNT) as u64 * 60_000) >> 19) as u32;
        let (cal_tap, cal_width, cal_valid) = calibration();
        Snapshot {
            id: r32(offset::ID),
            ck_khz_req: r32(offset::CK_KHZ),
            ck_khz_meas: khz,
            ready: s & STATUS_READY != 0,
            locked: s & STATUS_LOCKED != 0,
            calibrated: s & STATUS_CAL != 0,
            busy: s & STATUS_BUSY != 0,
            hr_busy: s & STATUS_HR_BUSY != 0,
            last_op: (s >> 12) as u8,
            last_code: ((s >> 8) & 0xf) as u8,
            errors: r16(offset::ERRORS),
            state: r16(offset::STATE),
            cal_tap,
            cal_width,
            cal_valid,
        }
    }

    pub fn win_get(off: u32) -> u8 {
        // SAFETY: inside the 4 KiB HYPERRAM_WINDOW; callers keep `off` < WINDOW.
        unsafe { read_volatile((WIN + off as usize) as *const u8) }
    }

    pub fn win_put(off: u32, value: u8) {
        // SAFETY: as `win_get`.
        unsafe { write_volatile((WIN + off as usize) as *mut u8, value) }
    }

    /// The window's first 32-bit word, little-endian, in one access.
    pub fn win_word0() -> u32 {
        // SAFETY: the window's first word.
        unsafe { read_volatile(WIN as *const u32) }
    }

    pub fn win_set_word0(value: u32) {
        // SAFETY: as `win_word0`.
        unsafe { write_volatile(WIN as *mut u32, value) }
    }

    fn now() -> u32 {
        let t: u32;
        // SAFETY: reads the `time` CSR, which ticks at SYNC_HZ.
        unsafe { core::arch::asm!("rdtime {0}", out(reg) t) };
        t
    }

    fn ticks_from_us(us: u32) -> u32 {
        us * (SYNC_HZ / 1_000_000)
    }

    /// Bound for an op moving `bytes`: words + 38 hr cycles per transaction (latency
    /// 13 + 25 fixed; one per 1 KiB counts more than the 1336-byte bursts need), in
    /// ticks via HR_PER_TICK, + 64 for the handshake and polling; x1.25. Expiry:
    /// CODE_TIMEOUT. A read that loses words also spends them in the tail fill and can
    /// outlast this; the gateware then still reports it.
    pub fn op_ticks(bytes: u32) -> u32 {
        let hr = bytes / 2 + 2 + ((bytes >> 10) + 1) * 38;
        5 * (hr / HR_PER_TICK + 64) / 4
    }

    /// CALIBRATE: one 2048-word write, then 128 taps x (DELAYF step <= 1032 + 2048-word
    /// read + 4 x 38) hr cycles = 416 k, in ticks; x1.25.
    pub const CAL_TICKS: u32 = 5 * (416_000 / HR_PER_TICK + 64) / 4;

    /// Ready after configuration: tLOCK2 15 ms (ECP5 DS-02012) + RESET# 4 us + the
    /// automatic calibration (CAL_TICKS); x1.25.
    pub fn boot_ticks() -> u32 {
        5 * (ticks_from_us(15_004) + CAL_TICKS) / 4
    }

    fn wait(mask: u16, want: u16, ticks: u32) -> Option<u16> {
        let start = now();
        loop {
            let s = status();
            if s & mask == want {
                return Some(s);
            }
            if now().wrapping_sub(start) > ticks {
                return None;
            }
        }
    }

    /// Ready, idle and calibrated, within `boot_ticks`.
    pub fn wait_ready() -> bool {
        let all = STATUS_READY | STATUS_CAL | STATUS_BUSY | STATUS_HR_BUSY;
        wait(all, STATUS_READY | STATUS_CAL, boot_ticks()).is_some()
    }

    /// Start `op` with whatever operands are already written; its code, or CODE_TIMEOUT.
    pub fn run(op: u8, ticks: u32) -> u8 {
        w8(offset::CMD, op);
        match wait(STATUS_BUSY, 0, ticks) {
            Some(s) => ((s >> 8) & 0xf) as u8,
            None => CODE_TIMEOUT,
        }
    }

    /// COPY_IN (HyperRAM -> window) or COPY_OUT, any alignment, `len` >= 1.
    pub fn copy(op: u8, hr: u32, win: u32, len: u32) -> bool {
        w32(offset::HR_ADDR, hr);
        // 4 bytes into a 2-byte footprint: the upper two are unmapped and ignored
        w32(offset::WIN_ADDR, win);
        w32(offset::LEN, len);
        run(op, op_ticks(len)) == 0
    }
}

/// RAM arrays standing in for the window and the part, for the QEMU target.
///
/// They do not survive a reset, so a staged image never boots under QEMU; the
/// header, CRC and bounds logic runs against the same code the board runs.
#[cfg(feature = "qemu")]
pub mod backend {
    use core::sync::atomic::{AtomicU8, Ordering};

    pub const OP_COPY_OUT: u8 = 1;
    pub const OP_COPY_IN: u8 = 2;
    pub const CODE_TIMEOUT: u8 = 0xff;
    const PART: usize = (super::IMAGE_BYTE + super::MAX_IMAGE) as usize + 4096;

    static WIN: [AtomicU8; 4096] = [const { AtomicU8::new(0) }; 4096];
    static RAM: [AtomicU8; PART] = [const { AtomicU8::new(0) }; PART];

    pub fn win_get(off: u32) -> u8 {
        WIN[off as usize & 4095].load(Ordering::Relaxed)
    }

    pub fn win_put(off: u32, value: u8) {
        WIN[off as usize & 4095].store(value, Ordering::Relaxed);
    }

    pub fn win_word0() -> u32 {
        (0..4).fold(0, |acc, i| acc | (win_get(i) as u32) << (8 * i))
    }

    pub fn win_set_word0(value: u32) {
        for i in 0..4 {
            win_put(i, (value >> (8 * i)) as u8);
        }
    }

    pub fn copy(op: u8, hr: u32, win: u32, len: u32) -> bool {
        let (hr, win, len) = (hr as usize, win as usize, len as usize);
        if hr + len > PART || win + len > 4096 {
            return false;
        }
        for i in 0..len {
            if op == OP_COPY_IN {
                WIN[win + i].store(RAM[hr + i].load(Ordering::Relaxed), Ordering::Relaxed);
            } else {
                RAM[hr + i].store(WIN[win + i].load(Ordering::Relaxed), Ordering::Relaxed);
            }
        }
        true
    }

    pub fn wait_ready() -> bool {
        true
    }
}
