#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    msip: Msip,
    _reserved1: [u8; 0x3fff],
    mtimecmp_lo: MtimecmpLo,
    mtimecmp_hi: MtimecmpHi,
    _reserved3: [u8; 0x7ff0],
    mtime_lo: MtimeLo,
    mtime_hi: MtimeHi,
}
impl RegisterBlock {
    #[doc = "0x00 - CLINT.MSIP, 1 bits at +0x00"]
    #[inline(always)]
    pub const fn msip(&self) -> &Msip {
        &self.msip
    }
    #[doc = "0x4000 - CLINT.MTIMECMP_LO, 32 bits at +0x4000"]
    #[inline(always)]
    pub const fn mtimecmp_lo(&self) -> &MtimecmpLo {
        &self.mtimecmp_lo
    }
    #[doc = "0x4004 - CLINT.MTIMECMP_HI, 32 bits at +0x4004"]
    #[inline(always)]
    pub const fn mtimecmp_hi(&self) -> &MtimecmpHi {
        &self.mtimecmp_hi
    }
    #[doc = "0xbff8 - CLINT.MTIME_LO, 32 bits at +0xbff8"]
    #[inline(always)]
    pub const fn mtime_lo(&self) -> &MtimeLo {
        &self.mtime_lo
    }
    #[doc = "0xbffc - CLINT.MTIME_HI, 32 bits at +0xbffc"]
    #[inline(always)]
    pub const fn mtime_hi(&self) -> &MtimeHi {
        &self.mtime_hi
    }
}
#[doc = "MSIP (rw) register accessor: CLINT.MSIP, 1 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`msip::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`msip::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@msip`] module"]
#[doc(alias = "MSIP")]
pub type Msip = crate::Reg<msip::MsipSpec>;
#[doc = "CLINT.MSIP, 1 bits at +0x00"]
pub mod msip;
#[doc = "MTIMECMP_LO (rw) register accessor: CLINT.MTIMECMP_LO, 32 bits at +0x4000\n\nYou can [`read`](crate::Reg::read) this register and get [`mtimecmp_lo::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`mtimecmp_lo::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@mtimecmp_lo`] module"]
#[doc(alias = "MTIMECMP_LO")]
pub type MtimecmpLo = crate::Reg<mtimecmp_lo::MtimecmpLoSpec>;
#[doc = "CLINT.MTIMECMP_LO, 32 bits at +0x4000"]
pub mod mtimecmp_lo;
#[doc = "MTIMECMP_HI (rw) register accessor: CLINT.MTIMECMP_HI, 32 bits at +0x4004\n\nYou can [`read`](crate::Reg::read) this register and get [`mtimecmp_hi::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`mtimecmp_hi::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@mtimecmp_hi`] module"]
#[doc(alias = "MTIMECMP_HI")]
pub type MtimecmpHi = crate::Reg<mtimecmp_hi::MtimecmpHiSpec>;
#[doc = "CLINT.MTIMECMP_HI, 32 bits at +0x4004"]
pub mod mtimecmp_hi;
#[doc = "MTIME_LO (r) register accessor: CLINT.MTIME_LO, 32 bits at +0xbff8\n\nYou can [`read`](crate::Reg::read) this register and get [`mtime_lo::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@mtime_lo`] module"]
#[doc(alias = "MTIME_LO")]
pub type MtimeLo = crate::Reg<mtime_lo::MtimeLoSpec>;
#[doc = "CLINT.MTIME_LO, 32 bits at +0xbff8"]
pub mod mtime_lo;
#[doc = "MTIME_HI (r) register accessor: CLINT.MTIME_HI, 32 bits at +0xbffc\n\nYou can [`read`](crate::Reg::read) this register and get [`mtime_hi::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@mtime_hi`] module"]
#[doc(alias = "MTIME_HI")]
pub type MtimeHi = crate::Reg<mtime_hi::MtimeHiSpec>;
#[doc = "CLINT.MTIME_HI, 32 bits at +0xbffc"]
pub mod mtime_hi;
