#[doc = "Register `STREAM_LEVEL` reader"]
pub type R = crate::R<StreamLevelSpec>;
#[doc = "Field `V` reader - v \\[22:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:22 - v \\[22:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 0x007f_ffff)
    }
}
#[doc = "HYPERRAM.STREAM_LEVEL, 23 bits at +0x7c\n\nYou can [`read`](crate::Reg::read) this register and get [`stream_level::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StreamLevelSpec;
impl crate::RegisterSpec for StreamLevelSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`stream_level::R`](R) reader structure"]
impl crate::Readable for StreamLevelSpec {}
#[doc = "`reset()` method sets STREAM_LEVEL to value 0"]
impl crate::Resettable for StreamLevelSpec {}
