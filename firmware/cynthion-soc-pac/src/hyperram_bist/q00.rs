#[doc = "Register `Q00` reader"]
pub type R = crate::R<Q00Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q00, 32 bits at +0x100\n\nYou can [`read`](crate::Reg::read) this register and get [`q00::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q00Spec;
impl crate::RegisterSpec for Q00Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q00::R`](R) reader structure"]
impl crate::Readable for Q00Spec {}
#[doc = "`reset()` method sets Q00 to value 0"]
impl crate::Resettable for Q00Spec {}
