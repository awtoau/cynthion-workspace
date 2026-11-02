#[doc = "Register `CTRL` writer"]
pub type W = crate::W<CtrlSpec>;
#[doc = "Field `BUS_RESET` writer - bus_reset \\[0\\]"]
pub type BusResetW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `START` writer - start \\[1\\]"]
pub type StartW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `CLR_TX` writer - clr_tx \\[2\\]"]
pub type ClrTxW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `CLR_RX` writer - clr_rx \\[3\\]"]
pub type ClrRxW<'a, REG> = crate::BitWriter<'a, REG>;
impl W {
    #[doc = "Bit 0 - bus_reset \\[0\\]"]
    #[inline(always)]
    pub fn bus_reset(&mut self) -> BusResetW<'_, CtrlSpec> {
        BusResetW::new(self, 0)
    }
    #[doc = "Bit 1 - start \\[1\\]"]
    #[inline(always)]
    pub fn start(&mut self) -> StartW<'_, CtrlSpec> {
        StartW::new(self, 1)
    }
    #[doc = "Bit 2 - clr_tx \\[2\\]"]
    #[inline(always)]
    pub fn clr_tx(&mut self) -> ClrTxW<'_, CtrlSpec> {
        ClrTxW::new(self, 2)
    }
    #[doc = "Bit 3 - clr_rx \\[3\\]"]
    #[inline(always)]
    pub fn clr_rx(&mut self) -> ClrRxW<'_, CtrlSpec> {
        ClrRxW::new(self, 3)
    }
}
#[doc = "USB_HOST.CTRL, 4 bits at +0x01\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ctrl::W`](W). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CtrlSpec;
impl crate::RegisterSpec for CtrlSpec {
    type Ux = u8;
}
#[doc = "`write(|w| ..)` method takes [`ctrl::W`](W) writer structure"]
impl crate::Writable for CtrlSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets CTRL to value 0"]
impl crate::Resettable for CtrlSpec {}
