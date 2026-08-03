#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    cs_fell: CsFell,
    _reserved1: [u8; 0x01],
    sck_edges: SckEdges,
    dq_driven: DqDriven,
    _reserved3: [u8; 0x01],
    grants: Grants,
    oe_edges: OeEdges,
    clear: Clear,
}
impl RegisterBlock {
    #[doc = "0x00 - FLASH_PROBE.CS_FELL, 1 bits at +0x00"]
    #[inline(always)]
    pub const fn cs_fell(&self) -> &CsFell {
        &self.cs_fell
    }
    #[doc = "0x02 - FLASH_PROBE.SCK_EDGES, 16 bits at +0x02"]
    #[inline(always)]
    pub const fn sck_edges(&self) -> &SckEdges {
        &self.sck_edges
    }
    #[doc = "0x04 - FLASH_PROBE.DQ_DRIVEN, 1 bits at +0x04"]
    #[inline(always)]
    pub const fn dq_driven(&self) -> &DqDriven {
        &self.dq_driven
    }
    #[doc = "0x06 - FLASH_PROBE.GRANTS, 16 bits at +0x06"]
    #[inline(always)]
    pub const fn grants(&self) -> &Grants {
        &self.grants
    }
    #[doc = "0x08 - FLASH_PROBE.OE_EDGES, 16 bits at +0x08"]
    #[inline(always)]
    pub const fn oe_edges(&self) -> &OeEdges {
        &self.oe_edges
    }
    #[doc = "0x0a - FLASH_PROBE.CLEAR, 1 bits at +0x0a"]
    #[inline(always)]
    pub const fn clear(&self) -> &Clear {
        &self.clear
    }
}
#[doc = "CS_FELL (r) register accessor: FLASH_PROBE.CS_FELL, 1 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`cs_fell::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cs_fell`] module"]
#[doc(alias = "CS_FELL")]
pub type CsFell = crate::Reg<cs_fell::CsFellSpec>;
#[doc = "FLASH_PROBE.CS_FELL, 1 bits at +0x00"]
pub mod cs_fell;
#[doc = "SCK_EDGES (r) register accessor: FLASH_PROBE.SCK_EDGES, 16 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`sck_edges::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@sck_edges`] module"]
#[doc(alias = "SCK_EDGES")]
pub type SckEdges = crate::Reg<sck_edges::SckEdgesSpec>;
#[doc = "FLASH_PROBE.SCK_EDGES, 16 bits at +0x02"]
pub mod sck_edges;
#[doc = "DQ_DRIVEN (r) register accessor: FLASH_PROBE.DQ_DRIVEN, 1 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`dq_driven::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@dq_driven`] module"]
#[doc(alias = "DQ_DRIVEN")]
pub type DqDriven = crate::Reg<dq_driven::DqDrivenSpec>;
#[doc = "FLASH_PROBE.DQ_DRIVEN, 1 bits at +0x04"]
pub mod dq_driven;
#[doc = "GRANTS (r) register accessor: FLASH_PROBE.GRANTS, 16 bits at +0x06\n\nYou can [`read`](crate::Reg::read) this register and get [`grants::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@grants`] module"]
#[doc(alias = "GRANTS")]
pub type Grants = crate::Reg<grants::GrantsSpec>;
#[doc = "FLASH_PROBE.GRANTS, 16 bits at +0x06"]
pub mod grants;
#[doc = "OE_EDGES (r) register accessor: FLASH_PROBE.OE_EDGES, 16 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`oe_edges::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@oe_edges`] module"]
#[doc(alias = "OE_EDGES")]
pub type OeEdges = crate::Reg<oe_edges::OeEdgesSpec>;
#[doc = "FLASH_PROBE.OE_EDGES, 16 bits at +0x08"]
pub mod oe_edges;
#[doc = "CLEAR (w) register accessor: FLASH_PROBE.CLEAR, 1 bits at +0x0a\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`clear::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@clear`] module"]
#[doc(alias = "CLEAR")]
pub type Clear = crate::Reg<clear::ClearSpec>;
#[doc = "FLASH_PROBE.CLEAR, 1 bits at +0x0a"]
pub mod clear;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const CS_FELL: usize = 0x00;
    pub const SCK_EDGES: usize = 0x02;
    pub const DQ_DRIVEN: usize = 0x04;
    pub const GRANTS: usize = 0x06;
    pub const OE_EDGES: usize = 0x08;
    pub const CLEAR: usize = 0x0a;
}
