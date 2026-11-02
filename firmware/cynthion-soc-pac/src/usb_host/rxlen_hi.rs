#[doc = "Register `RXLEN_HI` reader"]
pub type R = crate::R<RxlenHiSpec>;
#[doc = "Field `V` reader - v \\[7:0\\]"]
pub type VR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - v \\[7:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "USB_HOST.RXLEN_HI, 8 bits at +0x0d\n\nYou can [`read`](crate::Reg::read) this register and get [`rxlen_hi::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RxlenHiSpec;
impl crate::RegisterSpec for RxlenHiSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`rxlen_hi::R`](R) reader structure"]
impl crate::Readable for RxlenHiSpec {}
#[doc = "`reset()` method sets RXLEN_HI to value 0"]
impl crate::Resettable for RxlenHiSpec {}
