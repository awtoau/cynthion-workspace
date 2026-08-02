//! Re-link when the linker script changes.
//!
//! Cargo tracks source files and manifests; it does not know that `-C link-arg=-Tmemory.x`
//! makes `memory.x` an input. Without this, moving the BOOT/IMAGE boundary and rebuilding
//! reports "Finished" and leaves the previous binary in place -- a stale image that links
//! at the old address and is indistinguishable from a bad one until it runs.

fn main() {
    println!("cargo::rerun-if-changed=memory.x");
    println!("cargo::rerun-if-changed=build.rs");
}
