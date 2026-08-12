#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    ctrl: Ctrl,
    status: Status,
    rung0: Rung0,
    rung1: Rung1,
}
impl RegisterBlock {
    #[doc = "0x00 - HYPERRAM_CK.CTRL, 32 bits at +0x00"]
    #[inline(always)]
    pub const fn ctrl(&self) -> &Ctrl {
        &self.ctrl
    }
    #[doc = "0x04 - HYPERRAM_CK.STATUS, 32 bits at +0x04"]
    #[inline(always)]
    pub const fn status(&self) -> &Status {
        &self.status
    }
    #[doc = "0x08 - HYPERRAM_CK.RUNG0, 32 bits at +0x08"]
    #[inline(always)]
    pub const fn rung0(&self) -> &Rung0 {
        &self.rung0
    }
    #[doc = "0x0c - HYPERRAM_CK.RUNG1, 32 bits at +0x0c"]
    #[inline(always)]
    pub const fn rung1(&self) -> &Rung1 {
        &self.rung1
    }
}
#[doc = "CTRL (rw) register accessor: HYPERRAM_CK.CTRL, 32 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`ctrl::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ctrl::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ctrl`] module"]
#[doc(alias = "CTRL")]
pub type Ctrl = crate::Reg<ctrl::CtrlSpec>;
#[doc = "HYPERRAM_CK.CTRL, 32 bits at +0x00"]
pub mod ctrl;
#[doc = "STATUS (r) register accessor: HYPERRAM_CK.STATUS, 32 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@status`] module"]
#[doc(alias = "STATUS")]
pub type Status = crate::Reg<status::StatusSpec>;
#[doc = "HYPERRAM_CK.STATUS, 32 bits at +0x04"]
pub mod status;
#[doc = "RUNG0 (r) register accessor: HYPERRAM_CK.RUNG0, 32 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`rung0::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@rung0`] module"]
#[doc(alias = "RUNG0")]
pub type Rung0 = crate::Reg<rung0::Rung0Spec>;
#[doc = "HYPERRAM_CK.RUNG0, 32 bits at +0x08"]
pub mod rung0;
#[doc = "RUNG1 (r) register accessor: HYPERRAM_CK.RUNG1, 32 bits at +0x0c\n\nYou can [`read`](crate::Reg::read) this register and get [`rung1::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@rung1`] module"]
#[doc(alias = "RUNG1")]
pub type Rung1 = crate::Reg<rung1::Rung1Spec>;
#[doc = "HYPERRAM_CK.RUNG1, 32 bits at +0x0c"]
pub mod rung1;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const CTRL: usize = 0x00;
    pub const STATUS: usize = 0x04;
    pub const RUNG0: usize = 0x08;
    pub const RUNG1: usize = 0x0c;
}
