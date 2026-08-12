#[doc = "Register `Q09` reader"]
pub type R = crate::R<Q09Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q09, 32 bits at +0x124\n\nYou can [`read`](crate::Reg::read) this register and get [`q09::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q09Spec;
impl crate::RegisterSpec for Q09Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q09::R`](R) reader structure"]
impl crate::Readable for Q09Spec {}
#[doc = "`reset()` method sets Q09 to value 0"]
impl crate::Resettable for Q09Spec {}
