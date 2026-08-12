#[doc = "Register `Q1F` reader"]
pub type R = crate::R<Q1fSpec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q1F, 32 bits at +0x17c\n\nYou can [`read`](crate::Reg::read) this register and get [`q1f::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q1fSpec;
impl crate::RegisterSpec for Q1fSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q1f::R`](R) reader structure"]
impl crate::Readable for Q1fSpec {}
#[doc = "`reset()` method sets Q1F to value 0"]
impl crate::Resettable for Q1fSpec {}
