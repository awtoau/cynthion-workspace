#[doc = "Register `BUSY` reader"]
pub type R = crate::R<BusySpec>;
#[doc = "Field `COUNT` reader - count \\[31:0\\]"]
pub type CountR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - count \\[31:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "HYPERRAM_PROBE.BUSY, 32 bits at +0x0c\n\nYou can [`read`](crate::Reg::read) this register and get [`busy::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct BusySpec;
impl crate::RegisterSpec for BusySpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`busy::R`](R) reader structure"]
impl crate::Readable for BusySpec {}
#[doc = "`reset()` method sets BUSY to value 0"]
impl crate::Resettable for BusySpec {}
