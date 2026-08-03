#[doc = "Register `CYC` reader"]
pub type R = crate::R<CycSpec>;
#[doc = "Field `COUNT` reader - count \\[31:0\\]"]
pub type CountR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - count \\[31:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "HYPERRAM_PROBE.CYC, 32 bits at +0x18\n\nYou can [`read`](crate::Reg::read) this register and get [`cyc::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CycSpec;
impl crate::RegisterSpec for CycSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`cyc::R`](R) reader structure"]
impl crate::Readable for CycSpec {}
#[doc = "`reset()` method sets CYC to value 0"]
impl crate::Resettable for CycSpec {}
