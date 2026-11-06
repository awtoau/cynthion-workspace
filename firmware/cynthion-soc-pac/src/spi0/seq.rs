#[doc = "Register `SEQ` reader"]
pub type R = crate::R<SeqSpec>;
#[doc = "Field `V` reader - v \\[15:0\\]"]
pub type VR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - v \\[15:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "SPI0.SEQ, 16 bits at +0x30\n\nYou can [`read`](crate::Reg::read) this register and get [`seq::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct SeqSpec;
impl crate::RegisterSpec for SeqSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`seq::R`](R) reader structure"]
impl crate::Readable for SeqSpec {}
#[doc = "`reset()` method sets SEQ to value 0"]
impl crate::Resettable for SeqSpec {}
