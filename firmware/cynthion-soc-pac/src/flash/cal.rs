#[doc = "Register `CAL` reader"]
pub type R = crate::R<CalSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "FLASH.CAL, 32 bits at +0x2c\n\nYou can [`read`](crate::Reg::read) this register and get [`cal::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CalSpec;
impl crate::RegisterSpec for CalSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`cal::R`](R) reader structure"]
impl crate::Readable for CalSpec {}
#[doc = "`reset()` method sets CAL to value 0"]
impl crate::Resettable for CalSpec {}
