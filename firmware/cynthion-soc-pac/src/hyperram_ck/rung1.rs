#[doc = "Register `RUNG1` reader"]
pub type R = crate::R<Rung1Spec>;
#[doc = "Field `KHZ` reader - khz \\[31:0\\]"]
pub type KhzR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - khz \\[31:0\\]"]
    #[inline(always)]
    pub fn khz(&self) -> KhzR {
        KhzR::new(self.bits)
    }
}
#[doc = "HYPERRAM_CK.RUNG1, 32 bits at +0x0c\n\nYou can [`read`](crate::Reg::read) this register and get [`rung1::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Rung1Spec;
impl crate::RegisterSpec for Rung1Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`rung1::R`](R) reader structure"]
impl crate::Readable for Rung1Spec {}
#[doc = "`reset()` method sets RUNG1 to value 0"]
impl crate::Resettable for Rung1Spec {}
