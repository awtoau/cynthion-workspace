#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    magic: Magic,
    git: Git,
    built: Built,
    sync_hz: SyncHz,
    cpu: Cpu,
    usb_hz: UsbHz,
    die: Die,
}
impl RegisterBlock {
    #[doc = "0x00 - BOARD_GATEWARE.MAGIC, 32 bits at +0x00"]
    #[inline(always)]
    pub const fn magic(&self) -> &Magic {
        &self.magic
    }
    #[doc = "0x04 - BOARD_GATEWARE.GIT, 32 bits at +0x04"]
    #[inline(always)]
    pub const fn git(&self) -> &Git {
        &self.git
    }
    #[doc = "0x08 - BOARD_GATEWARE.BUILT, 32 bits at +0x08"]
    #[inline(always)]
    pub const fn built(&self) -> &Built {
        &self.built
    }
    #[doc = "0x0c - BOARD_GATEWARE.SYNC_HZ, 32 bits at +0x0c"]
    #[inline(always)]
    pub const fn sync_hz(&self) -> &SyncHz {
        &self.sync_hz
    }
    #[doc = "0x10 - BOARD_GATEWARE.CPU, 32 bits at +0x10"]
    #[inline(always)]
    pub const fn cpu(&self) -> &Cpu {
        &self.cpu
    }
    #[doc = "0x14 - BOARD_GATEWARE.USB_HZ, 32 bits at +0x14"]
    #[inline(always)]
    pub const fn usb_hz(&self) -> &UsbHz {
        &self.usb_hz
    }
    #[doc = "0x18 - BOARD_GATEWARE.DIE, 9 bits at +0x18"]
    #[inline(always)]
    pub const fn die(&self) -> &Die {
        &self.die
    }
}
#[doc = "MAGIC (r) register accessor: BOARD_GATEWARE.MAGIC, 32 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`magic::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@magic`] module"]
#[doc(alias = "MAGIC")]
pub type Magic = crate::Reg<magic::MagicSpec>;
#[doc = "BOARD_GATEWARE.MAGIC, 32 bits at +0x00"]
pub mod magic;
#[doc = "GIT (r) register accessor: BOARD_GATEWARE.GIT, 32 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`git::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@git`] module"]
#[doc(alias = "GIT")]
pub type Git = crate::Reg<git::GitSpec>;
#[doc = "BOARD_GATEWARE.GIT, 32 bits at +0x04"]
pub mod git;
#[doc = "BUILT (r) register accessor: BOARD_GATEWARE.BUILT, 32 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`built::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@built`] module"]
#[doc(alias = "BUILT")]
pub type Built = crate::Reg<built::BuiltSpec>;
#[doc = "BOARD_GATEWARE.BUILT, 32 bits at +0x08"]
pub mod built;
#[doc = "SYNC_HZ (r) register accessor: BOARD_GATEWARE.SYNC_HZ, 32 bits at +0x0c\n\nYou can [`read`](crate::Reg::read) this register and get [`sync_hz::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@sync_hz`] module"]
#[doc(alias = "SYNC_HZ")]
pub type SyncHz = crate::Reg<sync_hz::SyncHzSpec>;
#[doc = "BOARD_GATEWARE.SYNC_HZ, 32 bits at +0x0c"]
pub mod sync_hz;
#[doc = "CPU (r) register accessor: BOARD_GATEWARE.CPU, 32 bits at +0x10\n\nYou can [`read`](crate::Reg::read) this register and get [`cpu::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cpu`] module"]
#[doc(alias = "CPU")]
pub type Cpu = crate::Reg<cpu::CpuSpec>;
#[doc = "BOARD_GATEWARE.CPU, 32 bits at +0x10"]
pub mod cpu;
#[doc = "USB_HZ (r) register accessor: BOARD_GATEWARE.USB_HZ, 32 bits at +0x14\n\nYou can [`read`](crate::Reg::read) this register and get [`usb_hz::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@usb_hz`] module"]
#[doc(alias = "USB_HZ")]
pub type UsbHz = crate::Reg<usb_hz::UsbHzSpec>;
#[doc = "BOARD_GATEWARE.USB_HZ, 32 bits at +0x14"]
pub mod usb_hz;
#[doc = "DIE (r) register accessor: BOARD_GATEWARE.DIE, 9 bits at +0x18\n\nYou can [`read`](crate::Reg::read) this register and get [`die::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@die`] module"]
#[doc(alias = "DIE")]
pub type Die = crate::Reg<die::DieSpec>;
#[doc = "BOARD_GATEWARE.DIE, 9 bits at +0x18"]
pub mod die;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const MAGIC: usize = 0x00;
    pub const GIT: usize = 0x04;
    pub const BUILT: usize = 0x08;
    pub const SYNC_HZ: usize = 0x0c;
    pub const CPU: usize = 0x10;
    pub const USB_HZ: usize = 0x14;
    pub const DIE: usize = 0x18;
}
