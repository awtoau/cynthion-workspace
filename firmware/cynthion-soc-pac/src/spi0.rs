#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    phy: Phy,
    cs: Cs,
    status: Status,
    _reserved3: [u8; 0x02],
    data: Data,
    _reserved4: [u8; 0x10],
    hold: Hold,
}
impl RegisterBlock {
    #[doc = "0x00 - SPI0.PHY, 18 bits at +0x00"]
    #[inline(always)]
    pub const fn phy(&self) -> &Phy {
        &self.phy
    }
    #[doc = "0x04 - SPI0.CS, 1 bits at +0x04"]
    #[inline(always)]
    pub const fn cs(&self) -> &Cs {
        &self.cs
    }
    #[doc = "0x05 - SPI0.STATUS, 2 bits at +0x05"]
    #[inline(always)]
    pub const fn status(&self) -> &Status {
        &self.status
    }
    #[doc = "0x08..0x10 - SPI0.DATA, 64 bits at +0x08"]
    #[inline(always)]
    pub const fn data(&self) -> &Data {
        &self.data
    }
    #[doc = "0x20 - SPI0.HOLD, 1 bits at +0x20"]
    #[inline(always)]
    pub const fn hold(&self) -> &Hold {
        &self.hold
    }
}
#[doc = "PHY (rw) register accessor: SPI0.PHY, 18 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`phy::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`phy::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@phy`] module"]
#[doc(alias = "PHY")]
pub type Phy = crate::Reg<phy::PhySpec>;
#[doc = "SPI0.PHY, 18 bits at +0x00"]
pub mod phy;
#[doc = "CS (w) register accessor: SPI0.CS, 1 bits at +0x04\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`cs::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cs`] module"]
#[doc(alias = "CS")]
pub type Cs = crate::Reg<cs::CsSpec>;
#[doc = "SPI0.CS, 1 bits at +0x04"]
pub mod cs;
#[doc = "STATUS (r) register accessor: SPI0.STATUS, 2 bits at +0x05\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@status`] module"]
#[doc(alias = "STATUS")]
pub type Status = crate::Reg<status::StatusSpec>;
#[doc = "SPI0.STATUS, 2 bits at +0x05"]
pub mod status;
#[doc = "DATA (rw) register accessor: SPI0.DATA, 64 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`data::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`data::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@data`] module"]
#[doc(alias = "DATA")]
pub type Data = crate::Reg<data::DataSpec>;
#[doc = "SPI0.DATA, 64 bits at +0x08"]
pub mod data;
#[doc = "HOLD (rw) register accessor: SPI0.HOLD, 1 bits at +0x20\n\nYou can [`read`](crate::Reg::read) this register and get [`hold::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`hold::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@hold`] module"]
#[doc(alias = "HOLD")]
pub type Hold = crate::Reg<hold::HoldSpec>;
#[doc = "SPI0.HOLD, 1 bits at +0x20"]
pub mod hold;
