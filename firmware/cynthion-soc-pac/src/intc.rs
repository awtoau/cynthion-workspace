#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    enable: Enable,
    pending: Pending,
}
impl RegisterBlock {
    #[doc = "0x00 - INTC.ENABLE, 18 bits at +0x00"]
    #[inline(always)]
    pub const fn enable(&self) -> &Enable {
        &self.enable
    }
    #[doc = "0x04 - INTC.PENDING, 18 bits at +0x04"]
    #[inline(always)]
    pub const fn pending(&self) -> &Pending {
        &self.pending
    }
}
#[doc = "ENABLE (rw) register accessor: INTC.ENABLE, 18 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`enable::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`enable::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@enable`] module"]
#[doc(alias = "ENABLE")]
pub type Enable = crate::Reg<enable::EnableSpec>;
#[doc = "INTC.ENABLE, 18 bits at +0x00"]
pub mod enable;
#[doc = "PENDING (rw) register accessor: INTC.PENDING, 18 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`pending::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`pending::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@pending`] module"]
#[doc(alias = "PENDING")]
pub type Pending = crate::Reg<pending::PendingSpec>;
#[doc = "INTC.PENDING, 18 bits at +0x04"]
pub mod pending;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const ENABLE: usize = 0x00;
    pub const PENDING: usize = 0x04;
}
