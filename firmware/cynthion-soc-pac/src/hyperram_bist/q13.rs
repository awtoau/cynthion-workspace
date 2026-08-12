#[doc = "Register `Q13` reader"]
pub type R = crate::R<Q13Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q13, 32 bits at +0x14c\n\nYou can [`read`](crate::Reg::read) this register and get [`q13::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q13Spec;
impl crate::RegisterSpec for Q13Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q13::R`](R) reader structure"]
impl crate::Readable for Q13Spec {}
#[doc = "`reset()` method sets Q13 to value 0"]
impl crate::Resettable for Q13Spec {}
