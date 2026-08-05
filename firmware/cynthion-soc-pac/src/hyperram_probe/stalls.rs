#[doc = "Register `STALLS` reader"]
pub type R = crate::R<StallsSpec>;
#[doc = "Field `COUNT` reader - count \\[31:0\\]"]
pub type CountR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - count \\[31:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "HYPERRAM_PROBE.STALLS, 32 bits at +0x20\n\nYou can [`read`](crate::Reg::read) this register and get [`stalls::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StallsSpec;
impl crate::RegisterSpec for StallsSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`stalls::R`](R) reader structure"]
impl crate::Readable for StallsSpec {}
#[doc = "`reset()` method sets STALLS to value 0"]
impl crate::Resettable for StallsSpec {}
