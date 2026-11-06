#[doc = "Register `POLL_US` reader"]
pub type R = crate::R<PollUsSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "SPI0.POLL_US, 32 bits at +0x24\n\nYou can [`read`](crate::Reg::read) this register and get [`poll_us::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct PollUsSpec;
impl crate::RegisterSpec for PollUsSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`poll_us::R`](R) reader structure"]
impl crate::Readable for PollUsSpec {}
#[doc = "`reset()` method sets POLL_US to value 0"]
impl crate::Resettable for PollUsSpec {}
