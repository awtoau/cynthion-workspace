#[doc = "Register `Q04` reader"]
pub type R = crate::R<Q04Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q04, 32 bits at +0x110\n\nYou can [`read`](crate::Reg::read) this register and get [`q04::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q04Spec;
impl crate::RegisterSpec for Q04Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q04::R`](R) reader structure"]
impl crate::Readable for Q04Spec {}
#[doc = "`reset()` method sets Q04 to value 0"]
impl crate::Resettable for Q04Spec {}
