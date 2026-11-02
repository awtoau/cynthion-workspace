#[doc = "Register `TX_LAST` writer"]
pub type W = crate::W<TxLastSpec>;
#[doc = "Field `V` writer - v \\[7:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 8>;
impl W {
    #[doc = "Bits 0:7 - v \\[7:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, TxLastSpec> {
        VW::new(self, 0)
    }
}
#[doc = "USB_HOST.TX_LAST, 8 bits at +0x15\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tx_last::W`](W). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct TxLastSpec;
impl crate::RegisterSpec for TxLastSpec {
    type Ux = u8;
}
#[doc = "`write(|w| ..)` method takes [`tx_last::W`](W) writer structure"]
impl crate::Writable for TxLastSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets TX_LAST to value 0"]
impl crate::Resettable for TxLastSpec {}
