#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    mode: Mode,
    input: Input,
    output: Output,
    setclr: Setclr,
}
impl RegisterBlock {
    #[doc = "0x00 - BOARD_GPIO.MODE, 16 bits at +0x00"]
    #[inline(always)]
    pub const fn mode(&self) -> &Mode {
        &self.mode
    }
    #[doc = "0x02 - BOARD_GPIO.INPUT, 8 bits at +0x02"]
    #[inline(always)]
    pub const fn input(&self) -> &Input {
        &self.input
    }
    #[doc = "0x03 - BOARD_GPIO.OUTPUT, 8 bits at +0x03"]
    #[inline(always)]
    pub const fn output(&self) -> &Output {
        &self.output
    }
    #[doc = "0x04 - BOARD_GPIO.SETCLR, 16 bits at +0x04"]
    #[inline(always)]
    pub const fn setclr(&self) -> &Setclr {
        &self.setclr
    }
}
#[doc = "MODE (rw) register accessor: BOARD_GPIO.MODE, 16 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`mode::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`mode::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@mode`] module"]
#[doc(alias = "MODE")]
pub type Mode = crate::Reg<mode::ModeSpec>;
#[doc = "BOARD_GPIO.MODE, 16 bits at +0x00"]
pub mod mode;
#[doc = "INPUT (r) register accessor: BOARD_GPIO.INPUT, 8 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`input::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@input`] module"]
#[doc(alias = "INPUT")]
pub type Input = crate::Reg<input::InputSpec>;
#[doc = "BOARD_GPIO.INPUT, 8 bits at +0x02"]
pub mod input;
#[doc = "OUTPUT (rw) register accessor: BOARD_GPIO.OUTPUT, 8 bits at +0x03\n\nYou can [`read`](crate::Reg::read) this register and get [`output::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`output::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@output`] module"]
#[doc(alias = "OUTPUT")]
pub type Output = crate::Reg<output::OutputSpec>;
#[doc = "BOARD_GPIO.OUTPUT, 8 bits at +0x03"]
pub mod output;
#[doc = "SETCLR (w) register accessor: BOARD_GPIO.SETCLR, 16 bits at +0x04\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`setclr::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@setclr`] module"]
#[doc(alias = "SETCLR")]
pub type Setclr = crate::Reg<setclr::SetclrSpec>;
#[doc = "BOARD_GPIO.SETCLR, 16 bits at +0x04"]
pub mod setclr;
