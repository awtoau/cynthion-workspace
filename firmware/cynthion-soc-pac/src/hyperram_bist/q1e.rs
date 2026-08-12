#[doc = "Register `Q1E` reader"]
pub type R = crate::R<Q1eSpec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q1E, 32 bits at +0x178\n\nYou can [`read`](crate::Reg::read) this register and get [`q1e::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q1eSpec;
impl crate::RegisterSpec for Q1eSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q1e::R`](R) reader structure"]
impl crate::Readable for Q1eSpec {}
#[doc = "`reset()` method sets Q1E to value 0"]
impl crate::Resettable for Q1eSpec {}
