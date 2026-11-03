#[doc = "Register `FILL_LAST` reader"]
pub type R = crate::R<FillLastSpec>;
#[doc = "Field `V` reader - v \\[15:0\\]"]
pub type VR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - v \\[15:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "FLASH.FILL_LAST, 16 bits at +0x48\n\nYou can [`read`](crate::Reg::read) this register and get [`fill_last::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct FillLastSpec;
impl crate::RegisterSpec for FillLastSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`fill_last::R`](R) reader structure"]
impl crate::Readable for FillLastSpec {}
#[doc = "`reset()` method sets FILL_LAST to value 0"]
impl crate::Resettable for FillLastSpec {}
