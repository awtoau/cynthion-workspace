#[doc = "Register `TR_CTRL` writer"]
pub type W = crate::W<TrCtrlSpec>;
#[doc = "Field `V` writer - v \\[3:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 4>;
impl W {
    #[doc = "Bits 0:3 - v \\[3:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, TrCtrlSpec> {
        VW::new(self, 0)
    }
}
#[doc = "USB_HOST.TR_CTRL, 4 bits at +0x18\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tr_ctrl::W`](W). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct TrCtrlSpec;
impl crate::RegisterSpec for TrCtrlSpec {
    type Ux = u8;
}
#[doc = "`write(|w| ..)` method takes [`tr_ctrl::W`](W) writer structure"]
impl crate::Writable for TrCtrlSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets TR_CTRL to value 0"]
impl crate::Resettable for TrCtrlSpec {}
