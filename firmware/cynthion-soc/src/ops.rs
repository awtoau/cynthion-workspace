//! The device interface: binary request/response ops over the drivers (#303).
//!
//! Contract: `docs/device-interface.md`. The modules below are pure (no
//! `crate::` paths beyond the CRC) and run in the host tests.

#![allow(dead_code)]

pub(crate) mod dispatch;
pub(crate) mod engine;
pub(crate) mod frame;
pub(crate) mod proto;
