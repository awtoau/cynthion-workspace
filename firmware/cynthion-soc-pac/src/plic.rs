#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    _reserved0: [u8; 0x04],
    priority1: Priority1,
    _reserved1: [u8; 0x03],
    priority2: Priority2,
    _reserved2: [u8; 0x03],
    priority3: Priority3,
    _reserved3: [u8; 0x0ff3],
    pending: Pending,
    _reserved4: [u8; 0x0fff],
    enable: Enable,
    _reserved5: [u8; 0x001f_dfff],
    threshold: Threshold,
    _reserved6: [u8; 0x03],
    claim: Claim,
}
impl RegisterBlock {
    #[doc = "0x04 - PLIC.PRIORITY1, 3 bits at +0x04"]
    #[inline(always)]
    pub const fn priority1(&self) -> &Priority1 {
        &self.priority1
    }
    #[doc = "0x08 - PLIC.PRIORITY2, 3 bits at +0x08"]
    #[inline(always)]
    pub const fn priority2(&self) -> &Priority2 {
        &self.priority2
    }
    #[doc = "0x0c - PLIC.PRIORITY3, 3 bits at +0x0c"]
    #[inline(always)]
    pub const fn priority3(&self) -> &Priority3 {
        &self.priority3
    }
    #[doc = "0x1000 - PLIC.PENDING, 4 bits at +0x1000"]
    #[inline(always)]
    pub const fn pending(&self) -> &Pending {
        &self.pending
    }
    #[doc = "0x2000 - PLIC.ENABLE, 4 bits at +0x2000"]
    #[inline(always)]
    pub const fn enable(&self) -> &Enable {
        &self.enable
    }
    #[doc = "0x200000 - PLIC.THRESHOLD, 3 bits at +0x200000"]
    #[inline(always)]
    pub const fn threshold(&self) -> &Threshold {
        &self.threshold
    }
    #[doc = "0x200004 - PLIC.CLAIM, 8 bits at +0x200004"]
    #[inline(always)]
    pub const fn claim(&self) -> &Claim {
        &self.claim
    }
}
#[doc = "PRIORITY1 (rw) register accessor: PLIC.PRIORITY1, 3 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`priority1::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`priority1::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@priority1`] module"]
#[doc(alias = "PRIORITY1")]
pub type Priority1 = crate::Reg<priority1::Priority1Spec>;
#[doc = "PLIC.PRIORITY1, 3 bits at +0x04"]
pub mod priority1;
#[doc = "PRIORITY2 (rw) register accessor: PLIC.PRIORITY2, 3 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`priority2::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`priority2::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@priority2`] module"]
#[doc(alias = "PRIORITY2")]
pub type Priority2 = crate::Reg<priority2::Priority2Spec>;
#[doc = "PLIC.PRIORITY2, 3 bits at +0x08"]
pub mod priority2;
#[doc = "PRIORITY3 (rw) register accessor: PLIC.PRIORITY3, 3 bits at +0x0c\n\nYou can [`read`](crate::Reg::read) this register and get [`priority3::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`priority3::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@priority3`] module"]
#[doc(alias = "PRIORITY3")]
pub type Priority3 = crate::Reg<priority3::Priority3Spec>;
#[doc = "PLIC.PRIORITY3, 3 bits at +0x0c"]
pub mod priority3;
#[doc = "PENDING (r) register accessor: PLIC.PENDING, 4 bits at +0x1000\n\nYou can [`read`](crate::Reg::read) this register and get [`pending::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@pending`] module"]
#[doc(alias = "PENDING")]
pub type Pending = crate::Reg<pending::PendingSpec>;
#[doc = "PLIC.PENDING, 4 bits at +0x1000"]
pub mod pending;
#[doc = "ENABLE (rw) register accessor: PLIC.ENABLE, 4 bits at +0x2000\n\nYou can [`read`](crate::Reg::read) this register and get [`enable::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`enable::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@enable`] module"]
#[doc(alias = "ENABLE")]
pub type Enable = crate::Reg<enable::EnableSpec>;
#[doc = "PLIC.ENABLE, 4 bits at +0x2000"]
pub mod enable;
#[doc = "THRESHOLD (rw) register accessor: PLIC.THRESHOLD, 3 bits at +0x200000\n\nYou can [`read`](crate::Reg::read) this register and get [`threshold::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`threshold::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@threshold`] module"]
#[doc(alias = "THRESHOLD")]
pub type Threshold = crate::Reg<threshold::ThresholdSpec>;
#[doc = "PLIC.THRESHOLD, 3 bits at +0x200000"]
pub mod threshold;
#[doc = "CLAIM (rw) register accessor: PLIC.CLAIM, 8 bits at +0x200004\n\nYou can [`read`](crate::Reg::read) this register and get [`claim::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`claim::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@claim`] module"]
#[doc(alias = "CLAIM")]
pub type Claim = crate::Reg<claim::ClaimSpec>;
#[doc = "PLIC.CLAIM, 8 bits at +0x200004"]
pub mod claim;
