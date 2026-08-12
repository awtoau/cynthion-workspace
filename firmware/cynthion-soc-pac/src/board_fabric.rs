#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    die: Die,
    _reserved1: [u8; 0x02],
    bus_fault: BusFault,
}
impl RegisterBlock {
    #[doc = "0x00 - BOARD_FABRIC.DIE, 9 bits at +0x00"]
    #[inline(always)]
    pub const fn die(&self) -> &Die {
        &self.die
    }
    #[doc = "0x04 - BOARD_FABRIC.BUS_FAULT, 32 bits at +0x04"]
    #[inline(always)]
    pub const fn bus_fault(&self) -> &BusFault {
        &self.bus_fault
    }
}
#[doc = "DIE (r) register accessor: BOARD_FABRIC.DIE, 9 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`die::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@die`] module"]
#[doc(alias = "DIE")]
pub type Die = crate::Reg<die::DieSpec>;
#[doc = "BOARD_FABRIC.DIE, 9 bits at +0x00"]
pub mod die;
#[doc = "BUS_FAULT (r) register accessor: BOARD_FABRIC.BUS_FAULT, 32 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`bus_fault::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@bus_fault`] module"]
#[doc(alias = "BUS_FAULT")]
pub type BusFault = crate::Reg<bus_fault::BusFaultSpec>;
#[doc = "BOARD_FABRIC.BUS_FAULT, 32 bits at +0x04"]
pub mod bus_fault;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const DIE: usize = 0x00;
    pub const BUS_FAULT: usize = 0x04;
}
