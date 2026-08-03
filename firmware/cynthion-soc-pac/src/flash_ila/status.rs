#[doc = "Register `STATUS` reader"]
pub type R = crate::R<StatusSpec>;
#[doc = "Field `COMPLETE` reader - complete \\[0\\]"]
pub type CompleteR = crate::BitReader;
#[doc = "Field `SAMPLING` reader - sampling \\[1\\]"]
pub type SamplingR = crate::BitReader;
impl R {
    #[doc = "Bit 0 - complete \\[0\\]"]
    #[inline(always)]
    pub fn complete(&self) -> CompleteR {
        CompleteR::new((self.bits & 1) != 0)
    }
    #[doc = "Bit 1 - sampling \\[1\\]"]
    #[inline(always)]
    pub fn sampling(&self) -> SamplingR {
        SamplingR::new(((self.bits >> 1) & 1) != 0)
    }
}
#[doc = "FLASH_ILA.STATUS, 2 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StatusSpec;
impl crate::RegisterSpec for StatusSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`status::R`](R) reader structure"]
impl crate::Readable for StatusSpec {}
#[doc = "`reset()` method sets STATUS to value 0"]
impl crate::Resettable for StatusSpec {}
