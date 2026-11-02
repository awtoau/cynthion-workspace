#[doc = "Register `RX_LEVEL` reader"]
pub type R = crate::R<RxLevelSpec>;
#[doc = "Field `V` reader - v \\[7:0\\]"]
pub type VR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - v \\[7:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "USB_HOST.RX_LEVEL, 8 bits at +0x12\n\nYou can [`read`](crate::Reg::read) this register and get [`rx_level::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RxLevelSpec;
impl crate::RegisterSpec for RxLevelSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`rx_level::R`](R) reader structure"]
impl crate::Readable for RxLevelSpec {}
#[doc = "`reset()` method sets RX_LEVEL to value 0"]
impl crate::Resettable for RxLevelSpec {}
