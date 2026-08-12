#[doc = "Register `Q05` reader"]
pub type R = crate::R<Q05Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q05, 32 bits at +0x114\n\nYou can [`read`](crate::Reg::read) this register and get [`q05::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q05Spec;
impl crate::RegisterSpec for Q05Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q05::R`](R) reader structure"]
impl crate::Readable for Q05Spec {}
#[doc = "`reset()` method sets Q05 to value 0"]
impl crate::Resettable for Q05Spec {}
