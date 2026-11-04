#[doc = "Register `BAD_BITS` reader"]
pub type R = crate::R<BadBitsSpec>;
#[doc = "Field `V` reader - v \\[15:0\\]"]
pub type VR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - v \\[15:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.BAD_BITS, 16 bits at +0x64\n\nYou can [`read`](crate::Reg::read) this register and get [`bad_bits::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct BadBitsSpec;
impl crate::RegisterSpec for BadBitsSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`bad_bits::R`](R) reader structure"]
impl crate::Readable for BadBitsSpec {}
#[doc = "`reset()` method sets BAD_BITS to value 0"]
impl crate::Resettable for BadBitsSpec {}
