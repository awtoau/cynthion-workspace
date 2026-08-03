#[doc = "Register `MAX_RUN` reader"]
pub type R = crate::R<MaxRunSpec>;
#[doc = "Field `COUNT` reader - count \\[15:0\\]"]
pub type CountR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - count \\[15:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "HYPERRAM_PROBE.MAX_RUN, 16 bits at +0x06\n\nYou can [`read`](crate::Reg::read) this register and get [`max_run::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct MaxRunSpec;
impl crate::RegisterSpec for MaxRunSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`max_run::R`](R) reader structure"]
impl crate::Readable for MaxRunSpec {}
#[doc = "`reset()` method sets MAX_RUN to value 0"]
impl crate::Resettable for MaxRunSpec {}
