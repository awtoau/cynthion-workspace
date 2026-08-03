#[doc = "Register `SAMPLE` reader"]
pub type R = crate::R<SampleSpec>;
#[doc = "Field `BITS` reader - bits \\[7:0\\]"]
pub type BitsR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - bits \\[7:0\\]"]
    #[inline(always)]
    pub fn bits_(&self) -> BitsR {
        BitsR::new(self.bits)
    }
}
#[doc = "FLASH_ILA.SAMPLE, 8 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`sample::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct SampleSpec;
impl crate::RegisterSpec for SampleSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`sample::R`](R) reader structure"]
impl crate::Readable for SampleSpec {}
#[doc = "`reset()` method sets SAMPLE to value 0"]
impl crate::Resettable for SampleSpec {}
