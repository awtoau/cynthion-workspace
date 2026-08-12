#[doc = "Register `Q0E` reader"]
pub type R = crate::R<Q0eSpec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q0E, 32 bits at +0x138\n\nYou can [`read`](crate::Reg::read) this register and get [`q0e::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q0eSpec;
impl crate::RegisterSpec for Q0eSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q0e::R`](R) reader structure"]
impl crate::Readable for Q0eSpec {}
#[doc = "`reset()` method sets Q0E to value 0"]
impl crate::Resettable for Q0eSpec {}
