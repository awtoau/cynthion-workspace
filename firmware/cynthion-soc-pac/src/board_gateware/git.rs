#[doc = "Register `GIT` reader"]
pub type R = crate::R<GitSpec>;
#[doc = "Field `VALUE` reader - value \\[31:0\\]"]
pub type ValueR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - value \\[31:0\\]"]
    #[inline(always)]
    pub fn value(&self) -> ValueR {
        ValueR::new(self.bits)
    }
}
#[doc = "BOARD_GATEWARE.GIT, 32 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`git::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct GitSpec;
impl crate::RegisterSpec for GitSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`git::R`](R) reader structure"]
impl crate::Readable for GitSpec {}
#[doc = "`reset()` method sets GIT to value 0"]
impl crate::Resettable for GitSpec {}
