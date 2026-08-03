#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    address: Address,
    data: Data,
    control: Control,
    status: Status,
}
impl RegisterBlock {
    #[doc = "0x00 - BOARD_ULPI.ADDRESS, 6 bits at +0x00"]
    #[inline(always)]
    pub const fn address(&self) -> &Address {
        &self.address
    }
    #[doc = "0x01 - BOARD_ULPI.DATA, 8 bits at +0x01"]
    #[inline(always)]
    pub const fn data(&self) -> &Data {
        &self.data
    }
    #[doc = "0x02 - BOARD_ULPI.CONTROL, 2 bits at +0x02"]
    #[inline(always)]
    pub const fn control(&self) -> &Control {
        &self.control
    }
    #[doc = "0x03 - BOARD_ULPI.STATUS, 2 bits at +0x03"]
    #[inline(always)]
    pub const fn status(&self) -> &Status {
        &self.status
    }
}
#[doc = "ADDRESS (rw) register accessor: BOARD_ULPI.ADDRESS, 6 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`address::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`address::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@address`] module"]
#[doc(alias = "ADDRESS")]
pub type Address = crate::Reg<address::AddressSpec>;
#[doc = "BOARD_ULPI.ADDRESS, 6 bits at +0x00"]
pub mod address;
#[doc = "DATA (rw) register accessor: BOARD_ULPI.DATA, 8 bits at +0x01\n\nYou can [`read`](crate::Reg::read) this register and get [`data::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`data::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@data`] module"]
#[doc(alias = "DATA")]
pub type Data = crate::Reg<data::DataSpec>;
#[doc = "BOARD_ULPI.DATA, 8 bits at +0x01"]
pub mod data;
#[doc = "CONTROL (w) register accessor: BOARD_ULPI.CONTROL, 2 bits at +0x02\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`control::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@control`] module"]
#[doc(alias = "CONTROL")]
pub type Control = crate::Reg<control::ControlSpec>;
#[doc = "BOARD_ULPI.CONTROL, 2 bits at +0x02"]
pub mod control;
#[doc = "STATUS (r) register accessor: BOARD_ULPI.STATUS, 2 bits at +0x03\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@status`] module"]
#[doc(alias = "STATUS")]
pub type Status = crate::Reg<status::StatusSpec>;
#[doc = "BOARD_ULPI.STATUS, 2 bits at +0x03"]
pub mod status;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const ADDRESS: usize = 0x00;
    pub const DATA: usize = 0x01;
    pub const CONTROL: usize = 0x02;
    pub const STATUS: usize = 0x03;
}
