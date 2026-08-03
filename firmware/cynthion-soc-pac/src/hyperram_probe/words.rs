#[doc = "Register `WORDS` reader"]
pub type R = crate::R<WordsSpec>;
#[doc = "Field `COUNT` reader - count \\[15:0\\]"]
pub type CountR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - count \\[15:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "HYPERRAM_PROBE.WORDS, 16 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`words::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct WordsSpec;
impl crate::RegisterSpec for WordsSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`words::R`](R) reader structure"]
impl crate::Readable for WordsSpec {}
#[doc = "`reset()` method sets WORDS to value 0"]
impl crate::Resettable for WordsSpec {}
