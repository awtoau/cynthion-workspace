#[doc = "Register `XIP_FILLS` reader"]
pub type R = crate::R<XipFillsSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "SPI0.XIP_FILLS, 32 bits at +0x34\n\nYou can [`read`](crate::Reg::read) this register and get [`xip_fills::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct XipFillsSpec;
impl crate::RegisterSpec for XipFillsSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`xip_fills::R`](R) reader structure"]
impl crate::Readable for XipFillsSpec {}
#[doc = "`reset()` method sets XIP_FILLS to value 0"]
impl crate::Resettable for XipFillsSpec {}
