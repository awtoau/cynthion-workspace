#[doc = "Register `OE_EDGES` reader"]
pub type R = crate::R<OeEdgesSpec>;
#[doc = "Field `COUNT` reader - count \\[15:0\\]"]
pub type CountR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - count \\[15:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "FLASH_PROBE.OE_EDGES, 16 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`oe_edges::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct OeEdgesSpec;
impl crate::RegisterSpec for OeEdgesSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`oe_edges::R`](R) reader structure"]
impl crate::Readable for OeEdgesSpec {}
#[doc = "`reset()` method sets OE_EDGES to value 0"]
impl crate::Resettable for OeEdgesSpec {}
