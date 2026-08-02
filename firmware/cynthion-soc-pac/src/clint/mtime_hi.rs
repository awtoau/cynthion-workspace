#[doc = "Register `MTIME_HI` reader"]
pub type R = crate::R<MtimeHiSpec>;
#[doc = "Field `VALUE` reader - value \\[31:0\\]"]
pub type ValueR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - value \\[31:0\\]"]
    #[inline(always)]
    pub fn value(&self) -> ValueR {
        ValueR::new(self.bits)
    }
}
#[doc = "CLINT.MTIME_HI, 32 bits at +0xbffc\n\nYou can [`read`](crate::Reg::read) this register and get [`mtime_hi::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct MtimeHiSpec;
impl crate::RegisterSpec for MtimeHiSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`mtime_hi::R`](R) reader structure"]
impl crate::Readable for MtimeHiSpec {}
#[doc = "`reset()` method sets MTIME_HI to value 0"]
impl crate::Resettable for MtimeHiSpec {}
