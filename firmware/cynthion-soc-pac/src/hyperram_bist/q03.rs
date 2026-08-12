#[doc = "Register `Q03` reader"]
pub type R = crate::R<Q03Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q03, 32 bits at +0x10c\n\nYou can [`read`](crate::Reg::read) this register and get [`q03::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q03Spec;
impl crate::RegisterSpec for Q03Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q03::R`](R) reader structure"]
impl crate::Readable for Q03Spec {}
#[doc = "`reset()` method sets Q03 to value 0"]
impl crate::Resettable for Q03Spec {}
