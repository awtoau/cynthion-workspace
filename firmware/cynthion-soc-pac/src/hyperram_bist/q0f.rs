#[doc = "Register `Q0F` reader"]
pub type R = crate::R<Q0fSpec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q0F, 32 bits at +0x13c\n\nYou can [`read`](crate::Reg::read) this register and get [`q0f::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q0fSpec;
impl crate::RegisterSpec for Q0fSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q0f::R`](R) reader structure"]
impl crate::Readable for Q0fSpec {}
#[doc = "`reset()` method sets Q0F to value 0"]
impl crate::Resettable for Q0fSpec {}
