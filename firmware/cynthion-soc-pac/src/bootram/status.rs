#[doc = "Register `STATUS` reader"]
pub type R = crate::R<StatusSpec>;
#[doc = "Field `VALID` reader - valid \\[0\\]"]
pub type ValidR = crate::BitReader;
impl R {
    #[doc = "Bit 0 - valid \\[0\\]"]
    #[inline(always)]
    pub fn valid(&self) -> ValidR {
        ValidR::new((self.bits & 1) != 0)
    }
}
#[doc = "BOOTRAM.STATUS, 1 bits at +0x0c\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StatusSpec;
impl crate::RegisterSpec for StatusSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`status::R`](R) reader structure"]
impl crate::Readable for StatusSpec {}
#[doc = "`reset()` method sets STATUS to value 0"]
impl crate::Resettable for StatusSpec {}
