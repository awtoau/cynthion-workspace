#[doc = "Register `TR_DATA` reader"]
pub type R = crate::R<TrDataSpec>;
#[doc = "Field `V` reader - v \\[7:0\\]"]
pub type VR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - v \\[7:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "USB_HOST.TR_DATA, 8 bits at +0x1f\n\nYou can [`read`](crate::Reg::read) this register and get [`tr_data::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct TrDataSpec;
impl crate::RegisterSpec for TrDataSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`tr_data::R`](R) reader structure"]
impl crate::Readable for TrDataSpec {}
#[doc = "`reset()` method sets TR_DATA to value 0"]
impl crate::Resettable for TrDataSpec {}
