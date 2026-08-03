#[doc = "Register `STARTS` reader"]
pub type R = crate::R<StartsSpec>;
#[doc = "Field `COUNT` reader - count \\[15:0\\]"]
pub type CountR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - count \\[15:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "HYPERRAM_PROBE.STARTS, 16 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`starts::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StartsSpec;
impl crate::RegisterSpec for StartsSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`starts::R`](R) reader structure"]
impl crate::Readable for StartsSpec {}
#[doc = "`reset()` method sets STARTS to value 0"]
impl crate::Resettable for StartsSpec {}
