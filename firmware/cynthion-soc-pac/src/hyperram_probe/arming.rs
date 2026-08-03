#[doc = "Register `ARMING` reader"]
pub type R = crate::R<ArmingSpec>;
#[doc = "Field `COUNT` reader - count \\[31:0\\]"]
pub type CountR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - count \\[31:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "HYPERRAM_PROBE.ARMING, 32 bits at +0x14\n\nYou can [`read`](crate::Reg::read) this register and get [`arming::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct ArmingSpec;
impl crate::RegisterSpec for ArmingSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`arming::R`](R) reader structure"]
impl crate::Readable for ArmingSpec {}
#[doc = "`reset()` method sets ARMING to value 0"]
impl crate::Resettable for ArmingSpec {}
