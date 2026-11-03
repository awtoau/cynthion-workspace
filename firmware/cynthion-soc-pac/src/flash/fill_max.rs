#[doc = "Register `FILL_MAX` reader"]
pub type R = crate::R<FillMaxSpec>;
#[doc = "Field `V` reader - v \\[15:0\\]"]
pub type VR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - v \\[15:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "FLASH.FILL_MAX, 16 bits at +0x4a\n\nYou can [`read`](crate::Reg::read) this register and get [`fill_max::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct FillMaxSpec;
impl crate::RegisterSpec for FillMaxSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`fill_max::R`](R) reader structure"]
impl crate::Readable for FillMaxSpec {}
#[doc = "`reset()` method sets FILL_MAX to value 0"]
impl crate::Resettable for FillMaxSpec {}
