#[doc = "Register `Q0D` reader"]
pub type R = crate::R<Q0dSpec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q0D, 32 bits at +0x134\n\nYou can [`read`](crate::Reg::read) this register and get [`q0d::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q0dSpec;
impl crate::RegisterSpec for Q0dSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q0d::R`](R) reader structure"]
impl crate::Readable for Q0dSpec {}
#[doc = "`reset()` method sets Q0D to value 0"]
impl crate::Resettable for Q0dSpec {}
