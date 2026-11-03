#[doc = "Register `WORDS` reader"]
pub type R = crate::R<WordsSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.WORDS, 32 bits at +0x4c\n\nYou can [`read`](crate::Reg::read) this register and get [`words::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct WordsSpec;
impl crate::RegisterSpec for WordsSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`words::R`](R) reader structure"]
impl crate::Readable for WordsSpec {}
#[doc = "`reset()` method sets WORDS to value 0"]
impl crate::Resettable for WordsSpec {}
