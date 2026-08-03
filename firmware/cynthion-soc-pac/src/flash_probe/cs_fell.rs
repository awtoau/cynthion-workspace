#[doc = "Register `CS_FELL` reader"]
pub type R = crate::R<CsFellSpec>;
#[doc = "Field `SEEN` reader - seen \\[0\\]"]
pub type SeenR = crate::BitReader;
impl R {
    #[doc = "Bit 0 - seen \\[0\\]"]
    #[inline(always)]
    pub fn seen(&self) -> SeenR {
        SeenR::new((self.bits & 1) != 0)
    }
}
#[doc = "FLASH_PROBE.CS_FELL, 1 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`cs_fell::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CsFellSpec;
impl crate::RegisterSpec for CsFellSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`cs_fell::R`](R) reader structure"]
impl crate::Readable for CsFellSpec {}
#[doc = "`reset()` method sets CS_FELL to value 0"]
impl crate::Resettable for CsFellSpec {}
