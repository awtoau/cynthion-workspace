#[doc = "Register `ERRS` reader"]
pub type R = crate::R<ErrsSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.ERRS, 32 bits at +0x58\n\nYou can [`read`](crate::Reg::read) this register and get [`errs::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct ErrsSpec;
impl crate::RegisterSpec for ErrsSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`errs::R`](R) reader structure"]
impl crate::Readable for ErrsSpec {}
#[doc = "`reset()` method sets ERRS to value 0"]
impl crate::Resettable for ErrsSpec {}
