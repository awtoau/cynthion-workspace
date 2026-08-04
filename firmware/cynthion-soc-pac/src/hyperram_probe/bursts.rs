#[doc = "Register `BURSTS` reader"]
pub type R = crate::R<BurstsSpec>;
#[doc = "Field `COUNT` reader - count \\[15:0\\]"]
pub type CountR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - count \\[15:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "HYPERRAM_PROBE.BURSTS, 16 bits at +0x1e\n\nYou can [`read`](crate::Reg::read) this register and get [`bursts::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct BurstsSpec;
impl crate::RegisterSpec for BurstsSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`bursts::R`](R) reader structure"]
impl crate::Readable for BurstsSpec {}
#[doc = "`reset()` method sets BURSTS to value 0"]
impl crate::Resettable for BurstsSpec {}
