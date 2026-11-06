//! Configuration descriptor walk for the host engine (`usb_host.rs`).
//!
//! Pure: no register access, host-tested through `cynthion-soc-tests`.

/// Bytes of the configuration descriptor `find_endpoints` reads.
pub const CONFIG_MAX: usize = 64;

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

    #[test]
    fn interrupt_only_is_no_pair() {
        let c = config(&[&[endpoint(0x81, 3, 8), endpoint(0x01, 3, 8)]]);
        assert_eq!(bulk_pair(&c), None);
    }
}
