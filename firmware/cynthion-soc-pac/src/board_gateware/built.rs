#[doc = "Register `BUILT` reader"]
pub type R = crate::R<BuiltSpec>;
#[doc = "Field `VALUE` reader - value \\[31:0\\]"]
pub type ValueR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - value \\[31:0\\]"]
    #[inline(always)]
    pub fn value(&self) -> ValueR {
        ValueR::new(self.bits)
    }
}
#[doc = "BOARD_GATEWARE.BUILT, 32 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`built::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct BuiltSpec;
impl crate::RegisterSpec for BuiltSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`built::R`](R) reader structure"]
impl crate::Readable for BuiltSpec {}
#[doc = "`reset()` method sets BUILT to value 0"]
impl crate::Resettable for BuiltSpec {}
