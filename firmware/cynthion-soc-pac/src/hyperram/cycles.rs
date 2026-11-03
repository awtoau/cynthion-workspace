#[doc = "Register `CYCLES` reader"]
pub type R = crate::R<CyclesSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.CYCLES, 32 bits at +0x48\n\nYou can [`read`](crate::Reg::read) this register and get [`cycles::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CyclesSpec;
impl crate::RegisterSpec for CyclesSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`cycles::R`](R) reader structure"]
impl crate::Readable for CyclesSpec {}
#[doc = "`reset()` method sets CYCLES to value 0"]
impl crate::Resettable for CyclesSpec {}
