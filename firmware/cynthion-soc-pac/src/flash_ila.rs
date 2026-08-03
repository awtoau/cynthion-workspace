#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    status: Status,
    arm: Arm,
    index: Index,
    sample: Sample,
}
impl RegisterBlock {
    #[doc = "0x00 - FLASH_ILA.STATUS, 2 bits at +0x00"]
    #[inline(always)]
    pub const fn status(&self) -> &Status {
        &self.status
    }
    #[doc = "0x01 - FLASH_ILA.ARM, 1 bits at +0x01"]
    #[inline(always)]
    pub const fn arm(&self) -> &Arm {
        &self.arm
    }
    #[doc = "0x02 - FLASH_ILA.INDEX, 16 bits at +0x02"]
    #[inline(always)]
    pub const fn index(&self) -> &Index {
        &self.index
    }
    #[doc = "0x04 - FLASH_ILA.SAMPLE, 8 bits at +0x04"]
    #[inline(always)]
    pub const fn sample(&self) -> &Sample {
        &self.sample
    }
}
#[doc = "STATUS (r) register accessor: FLASH_ILA.STATUS, 2 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@status`] module"]
#[doc(alias = "STATUS")]
pub type Status = crate::Reg<status::StatusSpec>;
#[doc = "FLASH_ILA.STATUS, 2 bits at +0x00"]
pub mod status;
#[doc = "ARM (w) register accessor: FLASH_ILA.ARM, 1 bits at +0x01\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`arm::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@arm`] module"]
#[doc(alias = "ARM")]
pub type Arm = crate::Reg<arm::ArmSpec>;
#[doc = "FLASH_ILA.ARM, 1 bits at +0x01"]
pub mod arm;
#[doc = "INDEX (rw) register accessor: FLASH_ILA.INDEX, 16 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`index::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`index::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@index`] module"]
#[doc(alias = "INDEX")]
pub type Index = crate::Reg<index::IndexSpec>;
#[doc = "FLASH_ILA.INDEX, 16 bits at +0x02"]
pub mod index;
#[doc = "SAMPLE (r) register accessor: FLASH_ILA.SAMPLE, 8 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`sample::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@sample`] module"]
#[doc(alias = "SAMPLE")]
pub type Sample = crate::Reg<sample::SampleSpec>;
#[doc = "FLASH_ILA.SAMPLE, 8 bits at +0x04"]
pub mod sample;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const STATUS: usize = 0x00;
    pub const ARM: usize = 0x01;
    pub const INDEX: usize = 0x02;
    pub const SAMPLE: usize = 0x04;
}
