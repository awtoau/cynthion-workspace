#[doc = "Register `ARM` writer"]
pub type W = crate::W<ArmSpec>;
#[doc = "Field `STROBE` writer - strobe \\[0\\]"]
pub type StrobeW<'a, REG> = crate::BitWriter<'a, REG>;
impl W {
    #[doc = "Bit 0 - strobe \\[0\\]"]
    #[inline(always)]
    pub fn strobe(&mut self) -> StrobeW<'_, ArmSpec> {
        StrobeW::new(self, 0)
    }
}
#[doc = "FLASH_ILA.ARM, 1 bits at +0x01\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`arm::W`](W). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct ArmSpec;
impl crate::RegisterSpec for ArmSpec {
    type Ux = u8;
}
#[doc = "`write(|w| ..)` method takes [`arm::W`](W) writer structure"]
impl crate::Writable for ArmSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets ARM to value 0"]
impl crate::Resettable for ArmSpec {}
