#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    ctrl: Ctrl,
    tx: Tx,
    rx: Rx,
    rxcnt: Rxcnt,
}
impl RegisterBlock {
    #[doc = "0x00 - BOARD_SIDEBAND.CTRL, 8 bits at +0x00"]
    #[inline(always)]
    pub const fn ctrl(&self) -> &Ctrl {
        &self.ctrl
    }
    #[doc = "0x01 - BOARD_SIDEBAND.TX, 8 bits at +0x01"]
    #[inline(always)]
    pub const fn tx(&self) -> &Tx {
        &self.tx
    }
    #[doc = "0x02 - BOARD_SIDEBAND.RX, 8 bits at +0x02"]
    #[inline(always)]
    pub const fn rx(&self) -> &Rx {
        &self.rx
    }
    #[doc = "0x03 - BOARD_SIDEBAND.RXCNT, 8 bits at +0x03"]
    #[inline(always)]
    pub const fn rxcnt(&self) -> &Rxcnt {
        &self.rxcnt
    }
}
#[doc = "CTRL (rw) register accessor: BOARD_SIDEBAND.CTRL, 8 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`ctrl::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ctrl::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ctrl`] module"]
#[doc(alias = "CTRL")]
pub type Ctrl = crate::Reg<ctrl::CtrlSpec>;
#[doc = "BOARD_SIDEBAND.CTRL, 8 bits at +0x00"]
pub mod ctrl;
#[doc = "TX (rw) register accessor: BOARD_SIDEBAND.TX, 8 bits at +0x01\n\nYou can [`read`](crate::Reg::read) this register and get [`tx::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tx::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@tx`] module"]
#[doc(alias = "TX")]
pub type Tx = crate::Reg<tx::TxSpec>;
#[doc = "BOARD_SIDEBAND.TX, 8 bits at +0x01"]
pub mod tx;
#[doc = "RX (r) register accessor: BOARD_SIDEBAND.RX, 8 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`rx::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@rx`] module"]
#[doc(alias = "RX")]
pub type Rx = crate::Reg<rx::RxSpec>;
#[doc = "BOARD_SIDEBAND.RX, 8 bits at +0x02"]
pub mod rx;
#[doc = "RXCNT (r) register accessor: BOARD_SIDEBAND.RXCNT, 8 bits at +0x03\n\nYou can [`read`](crate::Reg::read) this register and get [`rxcnt::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@rxcnt`] module"]
#[doc(alias = "RXCNT")]
pub type Rxcnt = crate::Reg<rxcnt::RxcntSpec>;
#[doc = "BOARD_SIDEBAND.RXCNT, 8 bits at +0x03"]
pub mod rxcnt;
