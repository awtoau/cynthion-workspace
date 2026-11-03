#[doc = "Register `BUSY_CYCLES` reader"]
pub type R = crate::R<BusyCyclesSpec>;
#[doc = "Field `V` reader - v \\[28:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:28 - v \\[28:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 0x1fff_ffff)
    }
}
#[doc = "FLASH.BUSY_CYCLES, 29 bits at +0x38\n\nYou can [`read`](crate::Reg::read) this register and get [`busy_cycles::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct BusyCyclesSpec;
impl crate::RegisterSpec for BusyCyclesSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`busy_cycles::R`](R) reader structure"]
impl crate::Readable for BusyCyclesSpec {}
#[doc = "`reset()` method sets BUSY_CYCLES to value 0"]
impl crate::Resettable for BusyCyclesSpec {}
