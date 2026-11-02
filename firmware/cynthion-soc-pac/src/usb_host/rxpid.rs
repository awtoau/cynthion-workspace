#[doc = "Register `RXPID` reader"]
pub type R = crate::R<RxpidSpec>;
#[doc = "Field `PID` reader - pid \\[7:0\\]"]
pub type PidR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - pid \\[7:0\\]"]
    #[inline(always)]
    pub fn pid(&self) -> PidR {
        PidR::new(self.bits)
    }
}
#[doc = "USB_HOST.RXPID, 8 bits at +0x07\n\nYou can [`read`](crate::Reg::read) this register and get [`rxpid::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RxpidSpec;
impl crate::RegisterSpec for RxpidSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`rxpid::R`](R) reader structure"]
impl crate::Readable for RxpidSpec {}
#[doc = "`reset()` method sets RXPID to value 0"]
impl crate::Resettable for RxpidSpec {}
