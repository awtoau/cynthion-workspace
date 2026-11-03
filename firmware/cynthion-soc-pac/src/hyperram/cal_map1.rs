#[doc = "Register `CAL_MAP1` reader"]
pub type R = crate::R<CalMap1Spec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.CAL_MAP1, 32 bits at +0x3c\n\nYou can [`read`](crate::Reg::read) this register and get [`cal_map1::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CalMap1Spec;
impl crate::RegisterSpec for CalMap1Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`cal_map1::R`](R) reader structure"]
impl crate::Readable for CalMap1Spec {}
#[doc = "`reset()` method sets CAL_MAP1 to value 0"]
impl crate::Resettable for CalMap1Spec {}
