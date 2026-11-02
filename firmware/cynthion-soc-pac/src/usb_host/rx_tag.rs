#[doc = "Register `RX_TAG` reader"]
pub type R = crate::R<RxTagSpec>;
#[doc = "Field `TAG` reader - tag \\[0\\]"]
pub type TagR = crate::BitReader;
#[doc = "Field `VALID` reader - valid \\[1\\]"]
pub type ValidR = crate::BitReader;
impl R {
    #[doc = "Bit 0 - tag \\[0\\]"]
    #[inline(always)]
    pub fn tag(&self) -> TagR {
        TagR::new((self.bits & 1) != 0)
    }
    #[doc = "Bit 1 - valid \\[1\\]"]
    #[inline(always)]
    pub fn valid(&self) -> ValidR {
        ValidR::new(((self.bits >> 1) & 1) != 0)
    }
}
#[doc = "USB_HOST.RX_TAG, 2 bits at +0x11\n\nYou can [`read`](crate::Reg::read) this register and get [`rx_tag::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RxTagSpec;
impl crate::RegisterSpec for RxTagSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`rx_tag::R`](R) reader structure"]
impl crate::Readable for RxTagSpec {}
#[doc = "`reset()` method sets RX_TAG to value 0"]
impl crate::Resettable for RxTagSpec {}
