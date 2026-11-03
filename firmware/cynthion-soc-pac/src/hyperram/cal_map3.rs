#[doc = "Register `CAL_MAP3` reader"]
pub type R = crate::R<CalMap3Spec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.CAL_MAP3, 32 bits at +0x44\n\nYou can [`read`](crate::Reg::read) this register and get [`cal_map3::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CalMap3Spec;
impl crate::RegisterSpec for CalMap3Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`cal_map3::R`](R) reader structure"]
impl crate::Readable for CalMap3Spec {}
#[doc = "`reset()` method sets CAL_MAP3 to value 0"]
impl crate::Resettable for CalMap3Spec {}
