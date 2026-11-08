//! Runs `cynthion-soc`'s `#[cfg(test)] mod tests` blocks on this machine (#337).
//!
//! `cynthion-soc` is `#![no_std] #![no_main]` with its own panic handler and
//! RISC-V inline asm, so `cargo test` cannot build it for the host and its test
//! modules had never executed once. A test that never runs is indistinguishable
//! from a test that passes: `CR0[3]` in #335 was three lines of arithmetic in a
//! file that already had a test module.
//!
//! **This crate compiles nothing new.** Each `#[path]` below pulls in a source
//! file of `cynthion-soc` verbatim, so the code under test is the code that
//! ships -- no copy to drift. The pattern is the repo's own: `src/hyperram.rs`
//! is already shared into `firmware/cynthion-boot` the same way.
//!
//! **The rule for what may be listed here: no `crate::` paths and no target
//! asm.** A module that needs either is not pure logic and belongs behind one
//! that is. Register access stays on the board.
//!
//! Anything this crate's own `tests/` needs must be `pub` here; the firmware
//! sees these modules through its own `mod` declarations and is unaffected.

// The firmware reaches these through `pub(crate)`, which is this crate's whole
// contents rather than the firmware's -- so most items look unused from here.
#![allow(dead_code)]

#[path = "../../cynthion-soc/src/clock/hz.rs"]
pub mod clock_hz;

#[path = "../../cynthion-soc/src/log/format.rs"]
pub mod log_format;

#[path = "../../cynthion-soc/src/power_rails.rs"]
pub mod power_rails;

#[path = "../../cynthion-soc/src/vbus_gate.rs"]
pub mod vbus_gate;

#[path = "../../cynthion-soc/src/shell/parse.rs"]
pub mod parse;

#[path = "../../cynthion-soc/src/shell/rejoin.rs"]
pub mod rejoin;

#[path = "../../cynthion-soc/src/usb_desc.rs"]
pub mod usb_desc;

#[path = "../../cynthion-soc/src/pd_decode.rs"]
pub mod pd_decode;

#[path = "../../cynthion-soc/src/pd_verbs.rs"]
pub mod pd_verbs;

// The CRC the op frames use, exactly as `cynthion-soc` and `cynthion-boot` build it.
#[path = "../../cynthion-soc/src/hyperram.rs"]
pub mod hyperram;

#[path = "ops_pure.rs"]
pub mod ops;

#[path = "../../cynthion-soc/src/pd_proxy.rs"]
pub mod pd_proxy;
