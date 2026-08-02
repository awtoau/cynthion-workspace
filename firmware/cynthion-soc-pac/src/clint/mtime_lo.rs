#[doc = "Register `MTIME_LO` reader"]
pub type R = crate::R<MtimeLoSpec>;
#[doc = "Field `VALUE` reader - value \\[31:0\\]"]
pub type ValueR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - value \\[31:0\\]"]
    #[inline(always)]
    pub fn value(&self) -> ValueR {
        ValueR::new(self.bits)
    }
}
#[doc = "CLINT.MTIME_LO, 32 bits at +0xbff8\n\nYou can [`read`](crate::Reg::read) this register and get [`mtime_lo::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct MtimeLoSpec;
impl crate::RegisterSpec for MtimeLoSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`mtime_lo::R`](R) reader structure"]
impl crate::Readable for MtimeLoSpec {}
#[doc = "`reset()` method sets MTIME_LO to value 0"]
impl crate::Resettable for MtimeLoSpec {}
