#[doc = "Register `CHECKED` reader"]
pub type R = crate::R<CheckedSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.CHECKED, 32 bits at +0x54\n\nYou can [`read`](crate::Reg::read) this register and get [`checked::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CheckedSpec;
impl crate::RegisterSpec for CheckedSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`checked::R`](R) reader structure"]
impl crate::Readable for CheckedSpec {}
#[doc = "`reset()` method sets CHECKED to value 0"]
impl crate::Resettable for CheckedSpec {}
