#[doc = "Register `Q0B` reader"]
pub type R = crate::R<Q0bSpec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q0B, 32 bits at +0x12c\n\nYou can [`read`](crate::Reg::read) this register and get [`q0b::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q0bSpec;
impl crate::RegisterSpec for Q0bSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q0b::R`](R) reader structure"]
impl crate::Readable for Q0bSpec {}
#[doc = "`reset()` method sets Q0B to value 0"]
impl crate::Resettable for Q0bSpec {}
