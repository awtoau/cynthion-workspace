#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    prer_lo: PrerLo,
    prer_hi: PrerHi,
    ctr: Ctr,
    txr_rxr: TxrRxr,
    cr_sr: CrSr,
}
impl RegisterBlock {
    #[doc = "0x00 - BOARD_I2C.PRER_LO, 8 bits at +0x00"]
    #[inline(always)]
    pub const fn prer_lo(&self) -> &PrerLo {
        &self.prer_lo
    }
    #[doc = "0x01 - BOARD_I2C.PRER_HI, 8 bits at +0x01"]
    #[inline(always)]
    pub const fn prer_hi(&self) -> &PrerHi {
        &self.prer_hi
    }
    #[doc = "0x02 - BOARD_I2C.CTR, 8 bits at +0x02"]
    #[inline(always)]
    pub const fn ctr(&self) -> &Ctr {
        &self.ctr
    }
    #[doc = "0x03 - BOARD_I2C.TXR_RXR, 8 bits at +0x03"]
    #[inline(always)]
    pub const fn txr_rxr(&self) -> &TxrRxr {
        &self.txr_rxr
    }
    #[doc = "0x04 - BOARD_I2C.CR_SR, 8 bits at +0x04"]
    #[inline(always)]
    pub const fn cr_sr(&self) -> &CrSr {
        &self.cr_sr
    }
}
#[doc = "PRER_LO (rw) register accessor: BOARD_I2C.PRER_LO, 8 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`prer_lo::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`prer_lo::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@prer_lo`] module"]
#[doc(alias = "PRER_LO")]
pub type PrerLo = crate::Reg<prer_lo::PrerLoSpec>;
#[doc = "BOARD_I2C.PRER_LO, 8 bits at +0x00"]
pub mod prer_lo;
#[doc = "PRER_HI (rw) register accessor: BOARD_I2C.PRER_HI, 8 bits at +0x01\n\nYou can [`read`](crate::Reg::read) this register and get [`prer_hi::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`prer_hi::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@prer_hi`] module"]
#[doc(alias = "PRER_HI")]
pub type PrerHi = crate::Reg<prer_hi::PrerHiSpec>;
#[doc = "BOARD_I2C.PRER_HI, 8 bits at +0x01"]
pub mod prer_hi;
#[doc = "CTR (rw) register accessor: BOARD_I2C.CTR, 8 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`ctr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ctr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ctr`] module"]
#[doc(alias = "CTR")]
pub type Ctr = crate::Reg<ctr::CtrSpec>;
#[doc = "BOARD_I2C.CTR, 8 bits at +0x02"]
pub mod ctr;
#[doc = "TXR_RXR (rw) register accessor: BOARD_I2C.TXR_RXR, 8 bits at +0x03\n\nYou can [`read`](crate::Reg::read) this register and get [`txr_rxr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`txr_rxr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@txr_rxr`] module"]
#[doc(alias = "TXR_RXR")]
pub type TxrRxr = crate::Reg<txr_rxr::TxrRxrSpec>;
#[doc = "BOARD_I2C.TXR_RXR, 8 bits at +0x03"]
pub mod txr_rxr;
#[doc = "CR_SR (rw) register accessor: BOARD_I2C.CR_SR, 8 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`cr_sr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`cr_sr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cr_sr`] module"]
#[doc(alias = "CR_SR")]
pub type CrSr = crate::Reg<cr_sr::CrSrSpec>;
#[doc = "BOARD_I2C.CR_SR, 8 bits at +0x04"]
pub mod cr_sr;
