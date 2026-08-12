#[doc = "Register `Q10` reader"]
pub type R = crate::R<Q10Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q10, 32 bits at +0x140\n\nYou can [`read`](crate::Reg::read) this register and get [`q10::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q10Spec;
impl crate::RegisterSpec for Q10Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q10::R`](R) reader structure"]
impl crate::Readable for Q10Spec {}
#[doc = "`reset()` method sets Q10 to value 0"]
impl crate::Resettable for Q10Spec {}
