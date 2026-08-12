#[doc = "Register `Q1A` reader"]
pub type R = crate::R<Q1aSpec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q1A, 32 bits at +0x168\n\nYou can [`read`](crate::Reg::read) this register and get [`q1a::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q1aSpec;
impl crate::RegisterSpec for Q1aSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q1a::R`](R) reader structure"]
impl crate::Readable for Q1aSpec {}
#[doc = "`reset()` method sets Q1A to value 0"]
impl crate::Resettable for Q1aSpec {}
