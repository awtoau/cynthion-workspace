#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    sync_khz: SyncKhz,
    status: Status,
}
impl RegisterBlock {
    #[doc = "0x00 - BOARD_CLOCKS.SYNC_KHZ, 32 bits at +0x00"]
    #[inline(always)]
    pub const fn sync_khz(&self) -> &SyncKhz {
        &self.sync_khz
    }
    #[doc = "0x04 - BOARD_CLOCKS.STATUS, 32 bits at +0x04"]
    #[inline(always)]
    pub const fn status(&self) -> &Status {
        &self.status
    }
}
#[doc = "SYNC_KHZ (r) register accessor: BOARD_CLOCKS.SYNC_KHZ, 32 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`sync_khz::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@sync_khz`] module"]
#[doc(alias = "SYNC_KHZ")]
pub type SyncKhz = crate::Reg<sync_khz::SyncKhzSpec>;
#[doc = "BOARD_CLOCKS.SYNC_KHZ, 32 bits at +0x00"]
pub mod sync_khz;
#[doc = "STATUS (r) register accessor: BOARD_CLOCKS.STATUS, 32 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@status`] module"]
#[doc(alias = "STATUS")]
pub type Status = crate::Reg<status::StatusSpec>;
#[doc = "BOARD_CLOCKS.STATUS, 32 bits at +0x04"]
pub mod status;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const SYNC_KHZ: usize = 0x00;
    pub const STATUS: usize = 0x04;
}
