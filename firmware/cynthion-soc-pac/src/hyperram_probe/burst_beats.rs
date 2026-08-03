#[doc = "Register `BURST_BEATS` reader"]
pub type R = crate::R<BurstBeatsSpec>;
#[doc = "Field `COUNT` reader - count \\[15:0\\]"]
pub type CountR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - count \\[15:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "HYPERRAM_PROBE.BURST_BEATS, 16 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`burst_beats::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct BurstBeatsSpec;
impl crate::RegisterSpec for BurstBeatsSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`burst_beats::R`](R) reader structure"]
impl crate::Readable for BurstBeatsSpec {}
#[doc = "`reset()` method sets BURST_BEATS to value 0"]
impl crate::Resettable for BurstBeatsSpec {}
