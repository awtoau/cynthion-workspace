#[doc = "Register `Q06` reader"]
pub type R = crate::R<Q06Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q06, 32 bits at +0x118\n\nYou can [`read`](crate::Reg::read) this register and get [`q06::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q06Spec;
impl crate::RegisterSpec for Q06Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q06::R`](R) reader structure"]
impl crate::Readable for Q06Spec {}
#[doc = "`reset()` method sets Q06 to value 0"]
impl crate::Resettable for Q06Spec {}
