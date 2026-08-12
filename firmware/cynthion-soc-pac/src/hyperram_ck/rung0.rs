#[doc = "Register `RUNG0` reader"]
pub type R = crate::R<Rung0Spec>;
#[doc = "Field `KHZ` reader - khz \\[31:0\\]"]
pub type KhzR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - khz \\[31:0\\]"]
    #[inline(always)]
    pub fn khz(&self) -> KhzR {
        KhzR::new(self.bits)
    }
}
#[doc = "HYPERRAM_CK.RUNG0, 32 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`rung0::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Rung0Spec;
impl crate::RegisterSpec for Rung0Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`rung0::R`](R) reader structure"]
impl crate::Readable for Rung0Spec {}
#[doc = "`reset()` method sets RUNG0 to value 0"]
impl crate::Resettable for Rung0Spec {}
