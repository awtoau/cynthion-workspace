#[doc = "Register `CAL_MAP2` reader"]
pub type R = crate::R<CalMap2Spec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.CAL_MAP2, 32 bits at +0x40\n\nYou can [`read`](crate::Reg::read) this register and get [`cal_map2::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CalMap2Spec;
impl crate::RegisterSpec for CalMap2Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`cal_map2::R`](R) reader structure"]
impl crate::Readable for CalMap2Spec {}
#[doc = "`reset()` method sets CAL_MAP2 to value 0"]
impl crate::Resettable for CalMap2Spec {}
