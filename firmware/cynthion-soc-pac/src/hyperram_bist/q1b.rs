#[doc = "Register `Q1B` reader"]
pub type R = crate::R<Q1bSpec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q1B, 32 bits at +0x16c\n\nYou can [`read`](crate::Reg::read) this register and get [`q1b::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q1bSpec;
impl crate::RegisterSpec for Q1bSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q1b::R`](R) reader structure"]
impl crate::Readable for Q1bSpec {}
#[doc = "`reset()` method sets Q1B to value 0"]
impl crate::Resettable for Q1bSpec {}
