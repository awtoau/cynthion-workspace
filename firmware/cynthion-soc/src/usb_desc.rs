//! Configuration descriptor walk for the host engine (`usb_host.rs`).
//!
//! Pure: no register access, host-tested through `cynthion-soc-tests`.

/// Configuration descriptor buffer, sized for composite gadgets with margin (#627). A longer one is
/// still read whole (control drops bytes past the buffer); only its first 512 B are walked.
pub const CONFIG_MAX: usize = 512;
/// Configuration descriptor header (bLength 9); bytes 2-3 are wTotalLength.
pub const CONFIG_HEADER: usize = 9;

/// wTotalLength from a configuration descriptor header, `None` if it is not one.
pub fn total_length(h: &[u8]) -> Option<u16> {
    if h.len() < CONFIG_HEADER || h[0] < 9 || h[1] != 2 {
        return None;
    }
    Some(u16::from_le_bytes([h[2], h[3]]))
}

/// First bulk IN and first bulk OUT endpoint numbers.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct BulkPair {
    pub ep_in: u8,
    pub ep_out: u8,
}

/// Walk `c` (a configuration descriptor and what follows it) for the first bulk pair.
pub fn bulk_pair(c: &[u8]) -> Option<BulkPair> {
    let (mut at, mut ep_in, mut ep_out) = (0, None, None);
    while at + 7 <= c.len() && c[at] >= 2 {
        if c[at + 1] == 5 && c[at + 3] & 3 == 2 {
            let number = c[at + 2] & 15;
            if c[at + 2] & 0x80 != 0 {
                ep_in = ep_in.or(Some(number));
            } else {
                ep_out = ep_out.or(Some(number));
            }
        }
        at += c[at] as usize;
    }
    Some(BulkPair { ep_in: ep_in?, ep_out: ep_out? })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn endpoint(addr: u8, attr: u8, mps: u16) -> [u8; 7] {
        [7, 5, addr, attr, mps as u8, (mps >> 8) as u8, 0]
    }

    /// Config header, then one interface per `ifaces` entry, each with its endpoints.
    fn config(ifaces: &[&[[u8; 7]]]) -> Vec<u8> {
        let mut c = vec![9, 2, 0, 0, ifaces.len() as u8, 1, 0, 0x80, 50];
        for (n, eps) in ifaces.iter().enumerate() {
            c.extend_from_slice(&[9, 4, n as u8, 0, eps.len() as u8, 0xff, 0, 0, 0]);
            for ep in eps.iter() {
                c.extend_from_slice(ep);
            }
        }
        let total = c.len() as u16;
        c[2..4].copy_from_slice(&total.to_le_bytes());
        c
    }

    #[test]
    fn finds_the_first_bulk_pair() {
        let c = config(&[&[endpoint(0x81, 2, 512), endpoint(0x01, 2, 512), endpoint(0x82, 3, 8)]]);
        assert_eq!(bulk_pair(&c), Some(BulkPair { ep_in: 1, ep_out: 1 }));
    }

    /// Composite gadget: the bulk pair sits past byte 64 (as on the S20, #627).
    #[test]
    fn bulk_pair_past_byte_64_is_read() {
        let int3 = [endpoint(0x81, 3, 8), endpoint(0x82, 3, 8), endpoint(0x83, 3, 8)];
        let c = config(&[&int3, &int3, &[endpoint(0x84, 2, 512), endpoint(0x04, 2, 512)]]);
        assert!(c.len() > 64);
        let read = &c[..c.len().min(CONFIG_MAX)];
        assert_eq!(bulk_pair(read), Some(BulkPair { ep_in: 4, ep_out: 4 }));
    }

    /// FS bulk below 64 (USB 2.0 5.8.3 allows 8/16/32/64): the packet size is the descriptor's.
    #[test]
    fn bulk_mps_is_wmaxpacketsize() {
        let c = config(&[&[endpoint(0x81, 2, 32), endpoint(0x02, 2, 16)]]);
        let pair = bulk_pair(&c).unwrap();
        assert_eq!((pair.ep_in, pair.mps_in, pair.ep_out, pair.mps_out), (1, 32, 2, 16));
    }

    #[test]
    fn total_length_from_the_header() {
        let c = config(&[&[endpoint(0x81, 2, 64), endpoint(0x01, 2, 64)]]);
        assert_eq!(total_length(&c[..CONFIG_HEADER]), Some(c.len() as u16));
        assert_eq!(total_length(&[18, 1, 0, 2, 0, 0, 0, 64, 0]), None);
        assert_eq!(total_length(&c[..8]), None);
    }

    #[test]
    fn interrupt_only_is_no_pair() {
        let c = config(&[&[endpoint(0x81, 3, 8), endpoint(0x01, 3, 8)]]);
        assert_eq!(bulk_pair(&c), None);
    }
}
