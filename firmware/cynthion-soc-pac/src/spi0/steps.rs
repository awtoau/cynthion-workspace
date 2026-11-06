#[doc = "Register `STEPS` reader"]
pub type R = crate::R<StepsSpec>;
#[doc = "Field `V` reader - v \\[7:0\\]"]
pub type VR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - v \\[7:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "SPI0.STEPS, 8 bits at +0x2c\n\nYou can [`read`](crate::Reg::read) this register and get [`steps::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StepsSpec;
impl crate::RegisterSpec for StepsSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`steps::R`](R) reader structure"]
impl crate::Readable for StepsSpec {}
#[doc = "`reset()` method sets STEPS to value 0"]
impl crate::Resettable for StepsSpec {}
