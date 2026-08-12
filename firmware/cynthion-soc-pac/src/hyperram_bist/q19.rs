#[doc = "Register `Q19` reader"]
pub type R = crate::R<Q19Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q19, 32 bits at +0x164\n\nYou can [`read`](crate::Reg::read) this register and get [`q19::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q19Spec;
impl crate::RegisterSpec for Q19Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q19::R`](R) reader structure"]
impl crate::Readable for Q19Spec {}
#[doc = "`reset()` method sets Q19 to value 0"]
impl crate::Resettable for Q19Spec {}
