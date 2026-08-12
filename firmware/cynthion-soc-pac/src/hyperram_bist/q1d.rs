#[doc = "Register `Q1D` reader"]
pub type R = crate::R<Q1dSpec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q1D, 32 bits at +0x174\n\nYou can [`read`](crate::Reg::read) this register and get [`q1d::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q1dSpec;
impl crate::RegisterSpec for Q1dSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q1d::R`](R) reader structure"]
impl crate::Readable for Q1dSpec {}
#[doc = "`reset()` method sets Q1D to value 0"]
impl crate::Resettable for Q1dSpec {}
