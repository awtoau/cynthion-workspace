#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    addr: Addr,
    addr_rd: AddrRd,
    ctrl: Ctrl,
    _reserved3: [u8; 0x03],
    status: Status,
    data_lo: DataLo,
    data_hi: DataHi,
    _reserved6: [u8; 0x01],
    wdata: Wdata,
}
impl RegisterBlock {
    #[doc = "0x00 - BOOTRAM.ADDR, 32 bits at +0x00"]
    #[inline(always)]
    pub const fn addr(&self) -> &Addr {
        &self.addr
    }
    #[doc = "0x04 - BOOTRAM.ADDR_RD, 32 bits at +0x04"]
    #[inline(always)]
    pub const fn addr_rd(&self) -> &AddrRd {
        &self.addr_rd
    }
    #[doc = "0x08 - BOOTRAM.CTRL, 1 bits at +0x08"]
    #[inline(always)]
    pub const fn ctrl(&self) -> &Ctrl {
        &self.ctrl
    }
    #[doc = "0x0c - BOOTRAM.STATUS, 1 bits at +0x0c"]
    #[inline(always)]
    pub const fn status(&self) -> &Status {
        &self.status
    }
    #[doc = "0x0d - BOOTRAM.DATA_LO, 8 bits at +0x0d"]
    #[inline(always)]
    pub const fn data_lo(&self) -> &DataLo {
        &self.data_lo
    }
    #[doc = "0x0e - BOOTRAM.DATA_HI, 8 bits at +0x0e"]
    #[inline(always)]
    pub const fn data_hi(&self) -> &DataHi {
        &self.data_hi
    }
    #[doc = "0x10 - BOOTRAM.WDATA, 16 bits at +0x10"]
    #[inline(always)]
    pub const fn wdata(&self) -> &Wdata {
        &self.wdata
    }
}
#[doc = "ADDR (w) register accessor: BOOTRAM.ADDR, 32 bits at +0x00\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`addr::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@addr`] module"]
#[doc(alias = "ADDR")]
pub type Addr = crate::Reg<addr::AddrSpec>;
#[doc = "BOOTRAM.ADDR, 32 bits at +0x00"]
pub mod addr;
#[doc = "ADDR_RD (r) register accessor: BOOTRAM.ADDR_RD, 32 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`addr_rd::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@addr_rd`] module"]
#[doc(alias = "ADDR_RD")]
pub type AddrRd = crate::Reg<addr_rd::AddrRdSpec>;
#[doc = "BOOTRAM.ADDR_RD, 32 bits at +0x04"]
pub mod addr_rd;
#[doc = "CTRL (w) register accessor: BOOTRAM.CTRL, 1 bits at +0x08\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ctrl::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ctrl`] module"]
#[doc(alias = "CTRL")]
pub type Ctrl = crate::Reg<ctrl::CtrlSpec>;
#[doc = "BOOTRAM.CTRL, 1 bits at +0x08"]
pub mod ctrl;
#[doc = "STATUS (r) register accessor: BOOTRAM.STATUS, 1 bits at +0x0c\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@status`] module"]
#[doc(alias = "STATUS")]
pub type Status = crate::Reg<status::StatusSpec>;
#[doc = "BOOTRAM.STATUS, 1 bits at +0x0c"]
pub mod status;
#[doc = "DATA_LO (r) register accessor: BOOTRAM.DATA_LO, 8 bits at +0x0d\n\nYou can [`read`](crate::Reg::read) this register and get [`data_lo::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@data_lo`] module"]
#[doc(alias = "DATA_LO")]
pub type DataLo = crate::Reg<data_lo::DataLoSpec>;
#[doc = "BOOTRAM.DATA_LO, 8 bits at +0x0d"]
pub mod data_lo;
#[doc = "DATA_HI (r) register accessor: BOOTRAM.DATA_HI, 8 bits at +0x0e\n\nYou can [`read`](crate::Reg::read) this register and get [`data_hi::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@data_hi`] module"]
#[doc(alias = "DATA_HI")]
pub type DataHi = crate::Reg<data_hi::DataHiSpec>;
#[doc = "BOOTRAM.DATA_HI, 8 bits at +0x0e"]
pub mod data_hi;
#[doc = "WDATA (w) register accessor: BOOTRAM.WDATA, 16 bits at +0x10\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`wdata::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@wdata`] module"]
#[doc(alias = "WDATA")]
pub type Wdata = crate::Reg<wdata::WdataSpec>;
#[doc = "BOOTRAM.WDATA, 16 bits at +0x10"]
pub mod wdata;
