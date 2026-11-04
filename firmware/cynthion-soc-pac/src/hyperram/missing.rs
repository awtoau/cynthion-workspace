#[doc = "Register `MISSING` reader"]
pub type R = crate::R<MissingSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.MISSING, 32 bits at +0x68\n\nYou can [`read`](crate::Reg::read) this register and get [`missing::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct MissingSpec;
impl crate::RegisterSpec for MissingSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`missing::R`](R) reader structure"]
impl crate::Readable for MissingSpec {}
#[doc = "`reset()` method sets MISSING to value 0"]
impl crate::Resettable for MissingSpec {}
