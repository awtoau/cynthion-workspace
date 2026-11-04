#[doc = "Register `BAD_GOT_WANT` reader"]
pub type R = crate::R<BadGotWantSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.BAD_GOT_WANT, 32 bits at +0x60\n\nYou can [`read`](crate::Reg::read) this register and get [`bad_got_want::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct BadGotWantSpec;
impl crate::RegisterSpec for BadGotWantSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`bad_got_want::R`](R) reader structure"]
impl crate::Readable for BadGotWantSpec {}
#[doc = "`reset()` method sets BAD_GOT_WANT to value 0"]
impl crate::Resettable for BadGotWantSpec {}
