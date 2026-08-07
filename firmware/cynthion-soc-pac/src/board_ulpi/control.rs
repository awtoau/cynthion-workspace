#[doc = "Register `CONTROL` writer"]
pub type W = crate::W<ControlSpec>;
#[doc = "Field `START` writer - start \\[1:0\\]"]
pub type StartW<'a, REG> = crate::FieldWriter<'a, REG, 2>;
#[doc = "Field `PHY_RESET` writer - phy_reset \\[2\\]"]
pub type PhyResetW<'a, REG> = crate::BitWriter<'a, REG>;
impl W {
    #[doc = "Bits 0:1 - start \\[1:0\\]"]
    #[inline(always)]
    pub fn start(&mut self) -> StartW<'_, ControlSpec> {
        StartW::new(self, 0)
    }
    #[doc = "Bit 2 - phy_reset \\[2\\]"]
    #[inline(always)]
    pub fn phy_reset(&mut self) -> PhyResetW<'_, ControlSpec> {
        PhyResetW::new(self, 2)
    }
}
#[doc = "BOARD_ULPI.CONTROL, 3 bits at +0x02\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`control::W`](W). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct ControlSpec;
impl crate::RegisterSpec for ControlSpec {
    type Ux = u8;
}
#[doc = "`write(|w| ..)` method takes [`control::W`](W) writer structure"]
impl crate::Writable for ControlSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets CONTROL to value 0"]
impl crate::Resettable for ControlSpec {}
