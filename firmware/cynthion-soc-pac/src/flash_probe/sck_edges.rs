#[doc = "Register `SCK_EDGES` reader"]
pub type R = crate::R<SckEdgesSpec>;
#[doc = "Field `COUNT` reader - count \\[15:0\\]"]
pub type CountR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - count \\[15:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "FLASH_PROBE.SCK_EDGES, 16 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`sck_edges::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct SckEdgesSpec;
impl crate::RegisterSpec for SckEdgesSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`sck_edges::R`](R) reader structure"]
impl crate::Readable for SckEdgesSpec {}
#[doc = "`reset()` method sets SCK_EDGES to value 0"]
impl crate::Resettable for SckEdgesSpec {}
