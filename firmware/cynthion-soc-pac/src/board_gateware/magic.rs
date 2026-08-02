#[doc = "Register `MAGIC` reader"]
pub type R = crate::R<MagicSpec>;
#[doc = "Field `VALUE` reader - value \\[31:0\\]"]
pub type ValueR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - value \\[31:0\\]"]
    #[inline(always)]
    pub fn value(&self) -> ValueR {
        ValueR::new(self.bits)
    }
}
#[doc = "BOARD_GATEWARE.MAGIC, 32 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`magic::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct MagicSpec;
impl crate::RegisterSpec for MagicSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`magic::R`](R) reader structure"]
impl crate::Readable for MagicSpec {}
#[doc = "`reset()` method sets MAGIC to value 0"]
impl crate::Resettable for MagicSpec {}
