//! Argument parsing and small formatting helpers for the shell.
//!
//! Moved out of `main.rs` unchanged (#296). No allocation, no `core::fmt` width
//! machinery -- see the note on `help` for why that matters in this image.
//!
//! **Nothing here may name `crate::`.** That is what lets
//! `firmware/cynthion-soc-tests` include this file and run its tests on the host
//! (#337); `parse_limit`/`parse_port` needed `crate::power` and moved to
//! `shell/power.rs`, their only caller.

/// Format into a fixed byte buffer, so a column can be right-aligned without an
/// allocator.
///
/// `no_std` with no heap: `{:>7}` needs something with a known width, and there
/// is no `String` to build one in. Eight bytes covers `-99.999`, and anything
/// longer is truncated rather than panicking -- a clipped cell in a status table
/// is a display fault, and a panic in the shell is the board.
pub(crate) struct FixedWriter<'a> {
    buffer: &'a mut [u8],
    used: usize,
}

/// A NUL-padded cell as a `&str`, for `{:>7}`.
pub(crate) fn as_str(cell: &[u8]) -> &str {
    let end = cell.iter().position(|&b| b == 0).unwrap_or(cell.len());
    core::str::from_utf8(&cell[..end]).unwrap_or("?")
}

/// A decimal that may be negative. `parse_decimal` is unsigned, and a current
/// limit can legitimately be below zero -- the VBUS switch tree is
/// bidirectional, so a port can sink and its VSENSE code is signed.
///
/// Out of range is `None`, not a wrap. `4294967295` used to come back as `-1`
/// and `-2147483648` overflowed its own negation; both were reachable from
/// `power limit`, where a threshold silently becoming its own opposite sign is
/// an alert that fires on every sample or on none (#347).
pub(crate) fn parse_signed(text: &[u8]) -> Option<i32> {
    // TRIMMED FIRST. `parse_decimal` trims and this did not, so ` -3` split on
    // the space, fell to the unsigned arm and returned `None` -- a caller that
    // handed over a word with a space in front got a rejection it could not
    // explain.
    let text = trim(text);
    match text.split_first() {
        // `wrapping_neg` on the u32, not `-(v as i32)`: i32::MIN's magnitude is
        // one past i32::MAX and does not survive the cast.
        Some((b'-', rest)) => match parse_decimal(rest)? {
            magnitude if magnitude <= i32::MAX as u32 + 1 =>
                Some(magnitude.wrapping_neg() as i32),
            _ => None,
        },
        _ => match parse_decimal(text)? {
            value if value <= i32::MAX as u32 => Some(value as i32),
            _ => None,
        },
    }
}

/// Drop leading and trailing spaces. The line editor does not, and an argument
/// compared against `b"on"` must not have one on either end.
pub(crate) fn trim(text: &[u8]) -> &[u8] {
    let start = text.iter().position(|&b| b != b' ').unwrap_or(text.len());
    let end = text
        .iter()
        .rposition(|&b| b != b' ')
        .map_or(start, |i| i + 1);
    &text[start..end]
}

/// Parse an ASCII decimal number. `None` if empty or malformed.
///
/// Separate from `parse_hex` rather than a base parameter, because the two are
/// used for different things and confusing them is silent: `power floor aux 20`
/// meaning 32 mA would be a threshold nobody could explain. Addresses and
/// lengths are hex here; quantities a person states in engineering units are
/// decimal.
pub(crate) fn parse_decimal(text: &[u8]) -> Option<u32> {
    let text = trim(text);
    if text.is_empty() {
        return None;
    }
    let mut value: u32 = 0;
    for &byte in text {
        let digit = match byte {
            b'0'..=b'9' => byte - b'0',
            _ => return None,
        };
        value = value.checked_mul(10)?.checked_add(digit as u32)?;
    }
    Some(value)
}

/// Parse an ASCII hex number. `None` if empty or malformed -- better than a wrong
/// address silently read.
pub(crate) fn parse_hex(text: &[u8]) -> Option<u32> {
    let text = match text.iter().position(|&b| b != b' ') {
        Some(i) => &text[i..],
        None => return None,
    };
    let mut value: u32 = 0;
    let mut digits = 0;
    for &byte in text {
        let digit = match byte {
            b'0'..=b'9' => byte - b'0',
            b'a'..=b'f' => byte - b'a' + 10,
            b'A'..=b'F' => byte - b'A' + 10,
            b' ' => break,
            _ => return None,
        };
        value = value.checked_mul(16)?.checked_add(digit as u32)?;
        digits += 1;
    }
    if digits == 0 {
        None
    } else {
        Some(value)
    }
}

impl core::fmt::Write for FixedWriter<'_> {
    fn write_str(&mut self, text: &str) -> core::fmt::Result {
        for &byte in text.as_bytes() {
            if self.used >= self.buffer.len() {
                break;
            }
            self.buffer[self.used] = byte;
            self.used += 1;
        }
        Ok(())
    }
}


impl<'a> FixedWriter<'a> {
    pub(crate) fn new(buffer: &'a mut [u8]) -> Self {
        buffer.fill(0);
        FixedWriter { buffer, used: 0 }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use core::fmt::Write;

    /// #347: `bist phase clkos2 -3` took zero steps and reported success,
    /// because the count came back `None` and fell to `unwrap_or(0)`.
    ///
    /// The helper is INNOCENT for a bare `-3`; the argument never reached it.
    /// `shell/rejoin.rs` is where it went.
    #[test]
    fn a_negative_decimal_parses() {
        assert_eq!(parse_signed(b"-3"), Some(-3));
        assert_eq!(parse_signed(b"-1"), Some(-1));
        assert_eq!(parse_signed(b"-1000"), Some(-1000));
        assert_eq!(parse_signed(b"3"), Some(3));
        assert_eq!(parse_signed(b"0"), Some(0));
        assert_eq!(parse_signed(b"-0"), Some(0));
    }

    /// It IS wrong about spaces. `parse_decimal` trims and this did not, so the
    /// two disagreed about the same word.
    #[test]
    fn surrounding_spaces_are_dropped_the_same_as_the_unsigned_parser() {
        assert_eq!(parse_signed(b" -3"), Some(-3));
        assert_eq!(parse_signed(b"-3 "), Some(-3));
        assert_eq!(parse_signed(b"  -3  "), Some(-3));
        assert_eq!(parse_signed(b" 3 "), parse_decimal(b" 3 ").map(|v| v as i32));
    }

    /// And about range: a value past `i32::MAX` came back as a negative one.
    /// `power limit` takes these, and a threshold with the wrong sign alerts on
    /// every sample or on none.
    #[test]
    fn out_of_range_is_rejected_rather_than_wrapped() {
        assert_eq!(parse_signed(b"2147483647"), Some(i32::MAX));
        assert_eq!(parse_signed(b"2147483648"), None);
        assert_eq!(parse_signed(b"4294967295"), None);
        // The asymmetric end: i32::MIN's magnitude is one past i32::MAX.
        assert_eq!(parse_signed(b"-2147483648"), Some(i32::MIN));
        assert_eq!(parse_signed(b"-2147483649"), None);
    }

    #[test]
    fn a_sign_with_no_digits_is_not_a_number() {
        assert_eq!(parse_signed(b"-"), None);
        assert_eq!(parse_signed(b""), None);
        assert_eq!(parse_signed(b"  "), None);
        assert_eq!(parse_signed(b"--3"), None);
        assert_eq!(parse_signed(b"3-"), None);
        assert_eq!(parse_signed(b"1f"), None);
    }

    #[test]
    fn decimal_rejects_what_is_not_a_decimal() {
        assert_eq!(parse_decimal(b"0"), Some(0));
        assert_eq!(parse_decimal(b" 42 "), Some(42));
        assert_eq!(parse_decimal(b"4294967295"), Some(u32::MAX));
        // Overflow is `None`, not a wrap: `power floor aux <mA>` reads this.
        assert_eq!(parse_decimal(b"4294967296"), None);
        assert_eq!(parse_decimal(b""), None);
        assert_eq!(parse_decimal(b"1 2"), None);
        assert_eq!(parse_decimal(b"0x10"), None);
        // Hex digits are NOT decimal, so `power floor aux 1f` is refused rather
        // than read as 1.
        assert_eq!(parse_decimal(b"1f"), None);
    }

    #[test]
    fn hex_takes_both_cases_and_stops_at_a_space() {
        assert_eq!(parse_hex(b"ff"), Some(0xff));
        assert_eq!(parse_hex(b"FF"), Some(0xff));
        assert_eq!(parse_hex(b"  40000000"), Some(0x4000_0000));
        assert_eq!(parse_hex(b"ffffffff"), Some(u32::MAX));
        assert_eq!(parse_hex(b"100000000"), None);
        assert_eq!(parse_hex(b""), None);
        assert_eq!(parse_hex(b"   "), None);
        assert_eq!(parse_hex(b"g"), None);
        // Documented: a space ENDS the number, so a command can hand over the
        // tail. A trailing word is therefore ignored, not rejected.
        assert_eq!(parse_hex(b"1f 20"), Some(0x1f));
    }

    #[test]
    fn trim_handles_the_empty_and_all_space_cases() {
        assert_eq!(trim(b"  on  "), b"on");
        assert_eq!(trim(b"on"), b"on");
        assert_eq!(trim(b""), b"");
        assert_eq!(trim(b"    "), b"");
        // Interior spaces stay: a command's own grammar splits on them.
        assert_eq!(trim(b"  a b  "), b"a b");
    }

    #[test]
    fn a_cell_reads_back_to_its_nul() {
        assert_eq!(as_str(b"5.001\0\0\0"), "5.001");
        assert_eq!(as_str(b"12345678"), "12345678");
        assert_eq!(as_str(b"\0\0"), "");
        // Not UTF-8 is a display fault, not a panic in the shell.
        assert_eq!(as_str(&[0xff, 0xfe, 0]), "?");
    }

    /// Overrun TRUNCATES. A clipped cell is a display fault; a panic is the
    /// board.
    #[test]
    fn the_fixed_writer_truncates_instead_of_panicking() {
        let mut cell = [0u8; 8];
        let mut writer = FixedWriter::new(&mut cell);
        let _ = write!(writer, "{:>7}", "-99.999");
        assert_eq!(as_str(&cell), "-99.999");

        let mut cell = [0u8; 8];
        let mut writer = FixedWriter::new(&mut cell);
        let _ = write!(writer, "{}", "0123456789abcdef");
        assert_eq!(as_str(&cell), "01234567");
    }

    /// `new` clears, so a second render cannot leave the first one's tail behind.
    #[test]
    fn a_reused_buffer_does_not_keep_the_old_value() {
        let mut cell = [0u8; 8];
        let mut writer = FixedWriter::new(&mut cell);
        let _ = write!(writer, "{}", 12_345_678u32);
        let mut writer = FixedWriter::new(&mut cell);
        let _ = write!(writer, "{}", 1u32);
        assert_eq!(as_str(&cell), "1");
    }
}
