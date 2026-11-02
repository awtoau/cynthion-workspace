#[doc = "Register `TX_DATA` writer"]
pub type W = crate::W<TxDataSpec>;
#[doc = "Field `V` writer - v \\[7:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 8>;
impl W {
    #[doc = "Bits 0:7 - v \\[7:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, TxDataSpec> {
        VW::new(self, 0)
    }
}
#[doc = "USB_HOST.TX_DATA, 8 bits at +0x14\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tx_data::W`](W). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct TxDataSpec;
impl crate::RegisterSpec for TxDataSpec {
    type Ux = u8;
}
#[doc = "`write(|w| ..)` method takes [`tx_data::W`](W) writer structure"]
impl crate::Writable for TxDataSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets TX_DATA to value 0"]
impl crate::Resettable for TxDataSpec {}
