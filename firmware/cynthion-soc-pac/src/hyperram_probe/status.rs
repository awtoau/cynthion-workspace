#[doc = "Register `STATUS` reader"]
pub type R = crate::R<StatusSpec>;
#[doc = "Field `RWDS_ASK` reader - rwds_ask \\[0\\]"]
pub type RwdsAskR = crate::BitReader;
impl R {
    #[doc = "Bit 0 - rwds_ask \\[0\\]"]
    #[inline(always)]
    pub fn rwds_ask(&self) -> RwdsAskR {
        RwdsAskR::new((self.bits & 1) != 0)
    }
}
#[doc = "HYPERRAM_PROBE.STATUS, 1 bits at +0x1c\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StatusSpec;
impl crate::RegisterSpec for StatusSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`status::R`](R) reader structure"]
impl crate::Readable for StatusSpec {}
#[doc = "`reset()` method sets STATUS to value 0"]
impl crate::Resettable for StatusSpec {}
