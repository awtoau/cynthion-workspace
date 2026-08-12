#[doc = "Register `Q01` reader"]
pub type R = crate::R<Q01Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q01, 32 bits at +0x104\n\nYou can [`read`](crate::Reg::read) this register and get [`q01::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q01Spec;
impl crate::RegisterSpec for Q01Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q01::R`](R) reader structure"]
impl crate::Readable for Q01Spec {}
#[doc = "`reset()` method sets Q01 to value 0"]
impl crate::Resettable for Q01Spec {}
