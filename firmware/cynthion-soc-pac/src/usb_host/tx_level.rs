#[doc = "Register `TX_LEVEL` reader"]
pub type R = crate::R<TxLevelSpec>;
#[doc = "Field `V` reader - v \\[7:0\\]"]
pub type VR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - v \\[7:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "USB_HOST.TX_LEVEL, 8 bits at +0x17\n\nYou can [`read`](crate::Reg::read) this register and get [`tx_level::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct TxLevelSpec;
impl crate::RegisterSpec for TxLevelSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`tx_level::R`](R) reader structure"]
impl crate::Readable for TxLevelSpec {}
#[doc = "`reset()` method sets TX_LEVEL to value 0"]
impl crate::Resettable for TxLevelSpec {}
