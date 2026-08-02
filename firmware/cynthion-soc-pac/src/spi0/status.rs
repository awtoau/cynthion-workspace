#[doc = "Register `STATUS` reader"]
pub type R = crate::R<StatusSpec>;
#[doc = "Field `RX_READY` reader - rx_ready \\[0\\]"]
pub type RxReadyR = crate::BitReader;
#[doc = "Field `TX_READY` reader - tx_ready \\[1\\]"]
pub type TxReadyR = crate::BitReader;
impl R {
    #[doc = "Bit 0 - rx_ready \\[0\\]"]
    #[inline(always)]
    pub fn rx_ready(&self) -> RxReadyR {
        RxReadyR::new((self.bits & 1) != 0)
    }
    #[doc = "Bit 1 - tx_ready \\[1\\]"]
    #[inline(always)]
    pub fn tx_ready(&self) -> TxReadyR {
        TxReadyR::new(((self.bits >> 1) & 1) != 0)
    }
}
#[doc = "SPI0.STATUS, 2 bits at +0x05\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StatusSpec;
impl crate::RegisterSpec for StatusSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`status::R`](R) reader structure"]
impl crate::Readable for StatusSpec {}
#[doc = "`reset()` method sets STATUS to value 0"]
impl crate::Resettable for StatusSpec {}
