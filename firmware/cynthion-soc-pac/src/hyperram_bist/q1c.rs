#[doc = "Register `Q1C` reader"]
pub type R = crate::R<Q1cSpec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q1C, 32 bits at +0x170\n\nYou can [`read`](crate::Reg::read) this register and get [`q1c::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q1cSpec;
impl crate::RegisterSpec for Q1cSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q1c::R`](R) reader structure"]
impl crate::Readable for Q1cSpec {}
#[doc = "`reset()` method sets Q1C to value 0"]
impl crate::Resettable for Q1cSpec {}
