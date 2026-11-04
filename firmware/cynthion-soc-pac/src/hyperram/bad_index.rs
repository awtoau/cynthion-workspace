#[doc = "Register `BAD_INDEX` reader"]
pub type R = crate::R<BadIndexSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.BAD_INDEX, 32 bits at +0x5c\n\nYou can [`read`](crate::Reg::read) this register and get [`bad_index::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct BadIndexSpec;
impl crate::RegisterSpec for BadIndexSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`bad_index::R`](R) reader structure"]
impl crate::Readable for BadIndexSpec {}
#[doc = "`reset()` method sets BAD_INDEX to value 0"]
impl crate::Resettable for BadIndexSpec {}
