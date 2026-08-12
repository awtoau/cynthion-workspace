#[doc = "Register `Q16` reader"]
pub type R = crate::R<Q16Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q16, 32 bits at +0x158\n\nYou can [`read`](crate::Reg::read) this register and get [`q16::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q16Spec;
impl crate::RegisterSpec for Q16Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q16::R`](R) reader structure"]
impl crate::Readable for Q16Spec {}
#[doc = "`reset()` method sets Q16 to value 0"]
impl crate::Resettable for Q16Spec {}
