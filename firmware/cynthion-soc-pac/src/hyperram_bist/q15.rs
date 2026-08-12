#[doc = "Register `Q15` reader"]
pub type R = crate::R<Q15Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q15, 32 bits at +0x154\n\nYou can [`read`](crate::Reg::read) this register and get [`q15::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q15Spec;
impl crate::RegisterSpec for Q15Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q15::R`](R) reader structure"]
impl crate::Readable for Q15Spec {}
#[doc = "`reset()` method sets Q15 to value 0"]
impl crate::Resettable for Q15Spec {}
