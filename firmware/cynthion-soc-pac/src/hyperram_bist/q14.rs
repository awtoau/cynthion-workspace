#[doc = "Register `Q14` reader"]
pub type R = crate::R<Q14Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q14, 32 bits at +0x150\n\nYou can [`read`](crate::Reg::read) this register and get [`q14::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q14Spec;
impl crate::RegisterSpec for Q14Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q14::R`](R) reader structure"]
impl crate::Readable for Q14Spec {}
#[doc = "`reset()` method sets Q14 to value 0"]
impl crate::Resettable for Q14Spec {}
