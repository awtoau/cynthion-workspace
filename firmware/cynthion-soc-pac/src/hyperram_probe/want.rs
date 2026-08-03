#[doc = "Register `WANT` reader"]
pub type R = crate::R<WantSpec>;
#[doc = "Field `COUNT` reader - count \\[31:0\\]"]
pub type CountR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - count \\[31:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "HYPERRAM_PROBE.WANT, 32 bits at +0x10\n\nYou can [`read`](crate::Reg::read) this register and get [`want::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct WantSpec;
impl crate::RegisterSpec for WantSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`want::R`](R) reader structure"]
impl crate::Readable for WantSpec {}
#[doc = "`reset()` method sets WANT to value 0"]
impl crate::Resettable for WantSpec {}
