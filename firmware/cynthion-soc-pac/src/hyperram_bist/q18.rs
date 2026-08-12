#[doc = "Register `Q18` reader"]
pub type R = crate::R<Q18Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q18, 32 bits at +0x160\n\nYou can [`read`](crate::Reg::read) this register and get [`q18::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q18Spec;
impl crate::RegisterSpec for Q18Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q18::R`](R) reader structure"]
impl crate::Readable for Q18Spec {}
#[doc = "`reset()` method sets Q18 to value 0"]
impl crate::Resettable for Q18Spec {}
