//! Argument parsing and small formatting helpers for the shell.
//!
//! Moved out of `main.rs` unchanged (#296). No allocation, no `core::fmt` width
//! machinery -- see the note on `help` for why that matters in this image.

use crate::power;

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
/// Trims first. `parse_decimal` does, so without this a leading space killed a
/// negative and passed a positive -- ` -3` was `None` where ` 3` was `Some(3)`,
/// and a lever that reads zero looks like a lever that does nothing (#347).
pub(crate) fn parse_signed(text: &[u8]) -> Option<i32> {
    let text = trim(text);
    match text.split_first() {
        Some((b'-', rest)) => parse_decimal(rest).map(|v| -(v as i32)),
        _ => parse_decimal(text).map(|v| v as i32),
    }
}

/// Which limit a word names, or `None`.
pub(crate) fn parse_limit(name: &[u8]) -> Option<power::Limit> {
    power::Limit::ALL.iter().copied().find(|l| l.name().as_bytes() == name)
}

/// A port name to a PAC channel index. Named rather than numbered because
/// channel order is NOT connector order on this part -- channel 1 is TARGET_A,
/// not CONTROL -- and a bare index invites exactly that mistake.
pub(crate) fn parse_port(name: &[u8]) -> Option<usize> {
    power::PORTS.iter().position(|&p| p.as_bytes() == name)
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

    /// #347 asked whether `parse_signed` rejects negatives. It does not, and the
    /// `Some((b'-', rest))` arm is correct -- match ergonomics deref the `&u8`.
    #[test]
    fn signed_takes_negatives() {
        assert_eq!(parse_signed(b"3"), Some(3));
        assert_eq!(parse_signed(b"-3"), Some(-3));
        assert_eq!(parse_signed(b"-250"), Some(-250));
        assert_eq!(parse_signed(b"0"), Some(0));
        assert_eq!(parse_signed(b"-0"), Some(0));
    }

    /// The defect that WAS here: a leading space killed only the negative.
    #[test]
    fn signed_trims_like_decimal() {
        assert_eq!(parse_signed(b" 3"), Some(3));
        assert_eq!(parse_signed(b" -3"), Some(-3));
        assert_eq!(parse_signed(b"-3 "), Some(-3));
    }

    #[test]
    fn signed_rejects_malformed() {
        assert_eq!(parse_signed(b""), None);
        assert_eq!(parse_signed(b"-"), None);
        assert_eq!(parse_signed(b"12x"), None);
        assert_eq!(parse_signed(b"-12x"), None);
    }
}
