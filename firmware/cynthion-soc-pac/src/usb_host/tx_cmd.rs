#[doc = "Register `TX_CMD` writer"]
pub type W = crate::W<TxCmdSpec>;
#[doc = "Field `V` writer - v \\[7:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 8>;
impl W {
    #[doc = "Bits 0:7 - v \\[7:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, TxCmdSpec> {
        VW::new(self, 0)
    }
}
#[doc = "USB_HOST.TX_CMD, 8 bits at +0x16\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tx_cmd::W`](W). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct TxCmdSpec;
impl crate::RegisterSpec for TxCmdSpec {
    type Ux = u8;
}
#[doc = "`write(|w| ..)` method takes [`tx_cmd::W`](W) writer structure"]
impl crate::Writable for TxCmdSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets TX_CMD to value 0"]
impl crate::Resettable for TxCmdSpec {}
