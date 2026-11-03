#[doc = "Register `RESULT` reader"]
pub type R = crate::R<ResultSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "FLASH.RESULT, 32 bits at +0x1c\n\nYou can [`read`](crate::Reg::read) this register and get [`result::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct ResultSpec;
impl crate::RegisterSpec for ResultSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`result::R`](R) reader structure"]
impl crate::Readable for ResultSpec {}
#[doc = "`reset()` method sets RESULT to value 0"]
impl crate::Resettable for ResultSpec {}
