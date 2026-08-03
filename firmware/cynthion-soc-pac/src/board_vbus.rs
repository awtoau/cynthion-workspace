#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    ctrl: Ctrl,
    input: Input,
}
impl RegisterBlock {
    #[doc = "0x00 - BOARD_VBUS.CTRL, 8 bits at +0x00"]
    #[inline(always)]
    pub const fn ctrl(&self) -> &Ctrl {
        &self.ctrl
    }
    #[doc = "0x01 - BOARD_VBUS.INPUT, 8 bits at +0x01"]
    #[inline(always)]
    pub const fn input(&self) -> &Input {
        &self.input
    }
}
#[doc = "CTRL (rw) register accessor: BOARD_VBUS.CTRL, 8 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`ctrl::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ctrl::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ctrl`] module"]
#[doc(alias = "CTRL")]
pub type Ctrl = crate::Reg<ctrl::CtrlSpec>;
#[doc = "BOARD_VBUS.CTRL, 8 bits at +0x00"]
pub mod ctrl;
#[doc = "INPUT (rw) register accessor: BOARD_VBUS.INPUT, 8 bits at +0x01\n\nYou can [`read`](crate::Reg::read) this register and get [`input::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`input::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@input`] module"]
#[doc(alias = "INPUT")]
pub type Input = crate::Reg<input::InputSpec>;
#[doc = "BOARD_VBUS.INPUT, 8 bits at +0x01"]
pub mod input;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const CTRL: usize = 0x00;
    pub const INPUT: usize = 0x01;
}
