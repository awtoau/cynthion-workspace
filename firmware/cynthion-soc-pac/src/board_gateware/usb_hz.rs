#[doc = "Register `USB_HZ` reader"]
pub type R = crate::R<UsbHzSpec>;
#[doc = "Field `VALUE` reader - value \\[31:0\\]"]
pub type ValueR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - value \\[31:0\\]"]
    #[inline(always)]
    pub fn value(&self) -> ValueR {
        ValueR::new(self.bits)
    }
}
#[doc = "BOARD_GATEWARE.USB_HZ, 32 bits at +0x14\n\nYou can [`read`](crate::Reg::read) this register and get [`usb_hz::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct UsbHzSpec;
impl crate::RegisterSpec for UsbHzSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`usb_hz::R`](R) reader structure"]
impl crate::Readable for UsbHzSpec {}
#[doc = "`reset()` method sets USB_HZ to value 0"]
impl crate::Resettable for UsbHzSpec {}
