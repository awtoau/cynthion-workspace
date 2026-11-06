#[doc = "Register `CAL_US` reader"]
pub type R = crate::R<CalUsSpec>;
#[doc = "Field `V` reader - v \\[15:0\\]"]
pub type VR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - v \\[15:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "SPI0.CAL_US, 16 bits at +0x38\n\nYou can [`read`](crate::Reg::read) this register and get [`cal_us::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CalUsSpec;
impl crate::RegisterSpec for CalUsSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`cal_us::R`](R) reader structure"]
impl crate::Readable for CalUsSpec {}
#[doc = "`reset()` method sets CAL_US to value 0"]
impl crate::Resettable for CalUsSpec {}
