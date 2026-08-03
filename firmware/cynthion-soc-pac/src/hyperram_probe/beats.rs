#[doc = "Register `BEATS` reader"]
pub type R = crate::R<BeatsSpec>;
#[doc = "Field `COUNT` reader - count \\[15:0\\]"]
pub type CountR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - count \\[15:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "HYPERRAM_PROBE.BEATS, 16 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`beats::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct BeatsSpec;
impl crate::RegisterSpec for BeatsSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`beats::R`](R) reader structure"]
impl crate::Readable for BeatsSpec {}
#[doc = "`reset()` method sets BEATS to value 0"]
impl crate::Resettable for BeatsSpec {}
