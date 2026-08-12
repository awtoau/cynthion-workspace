#[doc = "Register `Q17` reader"]
pub type R = crate::R<Q17Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q17, 32 bits at +0x15c\n\nYou can [`read`](crate::Reg::read) this register and get [`q17::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q17Spec;
impl crate::RegisterSpec for Q17Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q17::R`](R) reader structure"]
impl crate::Readable for Q17Spec {}
#[doc = "`reset()` method sets Q17 to value 0"]
impl crate::Resettable for Q17Spec {}
