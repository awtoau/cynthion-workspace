#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    select: Select,
    lines: Lines,
}
impl RegisterBlock {
    #[doc = "0x00 - BOARD_I2C_MUX.SELECT, 2 bits at +0x00"]
    #[inline(always)]
    pub const fn select(&self) -> &Select {
        &self.select
    }
    #[doc = "0x01 - BOARD_I2C_MUX.LINES, 4 bits at +0x01"]
    #[inline(always)]
    pub const fn lines(&self) -> &Lines {
        &self.lines
    }
}
#[doc = "SELECT (rw) register accessor: BOARD_I2C_MUX.SELECT, 2 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`select::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`select::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@select`] module"]
#[doc(alias = "SELECT")]
pub type Select = crate::Reg<select::SelectSpec>;
#[doc = "BOARD_I2C_MUX.SELECT, 2 bits at +0x00"]
pub mod select;
#[doc = "LINES (r) register accessor: BOARD_I2C_MUX.LINES, 4 bits at +0x01\n\nYou can [`read`](crate::Reg::read) this register and get [`lines::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@lines`] module"]
#[doc(alias = "LINES")]
pub type Lines = crate::Reg<lines::LinesSpec>;
#[doc = "BOARD_I2C_MUX.LINES, 4 bits at +0x01"]
pub mod lines;
