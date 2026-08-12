#[doc = "Register `Q11` reader"]
pub type R = crate::R<Q11Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q11, 32 bits at +0x144\n\nYou can [`read`](crate::Reg::read) this register and get [`q11::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q11Spec;
impl crate::RegisterSpec for Q11Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q11::R`](R) reader structure"]
impl crate::Readable for Q11Spec {}
#[doc = "`reset()` method sets Q11 to value 0"]
impl crate::Resettable for Q11Spec {}
