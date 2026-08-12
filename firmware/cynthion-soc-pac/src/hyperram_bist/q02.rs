#[doc = "Register `Q02` reader"]
pub type R = crate::R<Q02Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q02, 32 bits at +0x108\n\nYou can [`read`](crate::Reg::read) this register and get [`q02::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q02Spec;
impl crate::RegisterSpec for Q02Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q02::R`](R) reader structure"]
impl crate::Readable for Q02Spec {}
#[doc = "`reset()` method sets Q02 to value 0"]
impl crate::Resettable for Q02Spec {}
