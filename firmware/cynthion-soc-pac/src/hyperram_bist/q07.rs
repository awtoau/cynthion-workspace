#[doc = "Register `Q07` reader"]
pub type R = crate::R<Q07Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q07, 32 bits at +0x11c\n\nYou can [`read`](crate::Reg::read) this register and get [`q07::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q07Spec;
impl crate::RegisterSpec for Q07Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q07::R`](R) reader structure"]
impl crate::Readable for Q07Spec {}
#[doc = "`reset()` method sets Q07 to value 0"]
impl crate::Resettable for Q07Spec {}
