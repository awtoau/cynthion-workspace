#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    rbr_thr: RbrThr,
    ier: Ier,
    iir_fcr: IirFcr,
    lcr: Lcr,
    mcr: Mcr,
    lsr: Lsr,
    msr: Msr,
    scr: Scr,
}
impl RegisterBlock {
    #[doc = "0x00 - APOLLO_UART.RBR_THR, 8 bits at +0x00"]
    #[inline(always)]
    pub const fn rbr_thr(&self) -> &RbrThr {
        &self.rbr_thr
    }
    #[doc = "0x01 - APOLLO_UART.IER, 8 bits at +0x01"]
    #[inline(always)]
    pub const fn ier(&self) -> &Ier {
        &self.ier
    }
    #[doc = "0x02 - APOLLO_UART.IIR_FCR, 8 bits at +0x02"]
    #[inline(always)]
    pub const fn iir_fcr(&self) -> &IirFcr {
        &self.iir_fcr
    }
    #[doc = "0x03 - APOLLO_UART.LCR, 8 bits at +0x03"]
    #[inline(always)]
    pub const fn lcr(&self) -> &Lcr {
        &self.lcr
    }
    #[doc = "0x04 - APOLLO_UART.MCR, 8 bits at +0x04"]
    #[inline(always)]
    pub const fn mcr(&self) -> &Mcr {
        &self.mcr
    }
    #[doc = "0x05 - APOLLO_UART.LSR, 8 bits at +0x05"]
    #[inline(always)]
    pub const fn lsr(&self) -> &Lsr {
        &self.lsr
    }
    #[doc = "0x06 - APOLLO_UART.MSR, 8 bits at +0x06"]
    #[inline(always)]
    pub const fn msr(&self) -> &Msr {
        &self.msr
    }
    #[doc = "0x07 - APOLLO_UART.SCR, 8 bits at +0x07"]
    #[inline(always)]
    pub const fn scr(&self) -> &Scr {
        &self.scr
    }
}
#[doc = "RBR_THR (rw) register accessor: APOLLO_UART.RBR_THR, 8 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`rbr_thr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`rbr_thr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@rbr_thr`] module"]
#[doc(alias = "RBR_THR")]
pub type RbrThr = crate::Reg<rbr_thr::RbrThrSpec>;
#[doc = "APOLLO_UART.RBR_THR, 8 bits at +0x00"]
pub mod rbr_thr;
#[doc = "IER (rw) register accessor: APOLLO_UART.IER, 8 bits at +0x01\n\nYou can [`read`](crate::Reg::read) this register and get [`ier::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ier::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ier`] module"]
#[doc(alias = "IER")]
pub type Ier = crate::Reg<ier::IerSpec>;
#[doc = "APOLLO_UART.IER, 8 bits at +0x01"]
pub mod ier;
#[doc = "IIR_FCR (rw) register accessor: APOLLO_UART.IIR_FCR, 8 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`iir_fcr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`iir_fcr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@iir_fcr`] module"]
#[doc(alias = "IIR_FCR")]
pub type IirFcr = crate::Reg<iir_fcr::IirFcrSpec>;
#[doc = "APOLLO_UART.IIR_FCR, 8 bits at +0x02"]
pub mod iir_fcr;
#[doc = "LCR (rw) register accessor: APOLLO_UART.LCR, 8 bits at +0x03\n\nYou can [`read`](crate::Reg::read) this register and get [`lcr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`lcr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@lcr`] module"]
#[doc(alias = "LCR")]
pub type Lcr = crate::Reg<lcr::LcrSpec>;
#[doc = "APOLLO_UART.LCR, 8 bits at +0x03"]
pub mod lcr;
#[doc = "MCR (rw) register accessor: APOLLO_UART.MCR, 8 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`mcr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`mcr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@mcr`] module"]
#[doc(alias = "MCR")]
pub type Mcr = crate::Reg<mcr::McrSpec>;
#[doc = "APOLLO_UART.MCR, 8 bits at +0x04"]
pub mod mcr;
#[doc = "LSR (r) register accessor: APOLLO_UART.LSR, 8 bits at +0x05\n\nYou can [`read`](crate::Reg::read) this register and get [`lsr::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@lsr`] module"]
#[doc(alias = "LSR")]
pub type Lsr = crate::Reg<lsr::LsrSpec>;
#[doc = "APOLLO_UART.LSR, 8 bits at +0x05"]
pub mod lsr;
#[doc = "MSR (r) register accessor: APOLLO_UART.MSR, 8 bits at +0x06\n\nYou can [`read`](crate::Reg::read) this register and get [`msr::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@msr`] module"]
#[doc(alias = "MSR")]
pub type Msr = crate::Reg<msr::MsrSpec>;
#[doc = "APOLLO_UART.MSR, 8 bits at +0x06"]
pub mod msr;
#[doc = "SCR (rw) register accessor: APOLLO_UART.SCR, 8 bits at +0x07\n\nYou can [`read`](crate::Reg::read) this register and get [`scr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`scr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@scr`] module"]
#[doc(alias = "SCR")]
pub type Scr = crate::Reg<scr::ScrSpec>;
#[doc = "APOLLO_UART.SCR, 8 bits at +0x07"]
pub mod scr;
