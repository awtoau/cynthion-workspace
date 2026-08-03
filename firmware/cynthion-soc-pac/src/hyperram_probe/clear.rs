#[doc = "Register `CLEAR` writer"]
pub type W = crate::W<ClearSpec>;
#[doc = "Field `STROBE` writer - strobe \\[0\\]"]
pub type StrobeW<'a, REG> = crate::BitWriter<'a, REG>;
impl W {
    #[doc = "Bit 0 - strobe \\[0\\]"]
    #[inline(always)]
    pub fn strobe(&mut self) -> StrobeW<'_, ClearSpec> {
        StrobeW::new(self, 0)
    }
}
#[doc = "HYPERRAM_PROBE.CLEAR, 1 bits at +0x1c\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`clear::W`](W). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct ClearSpec;
impl crate::RegisterSpec for ClearSpec {
    type Ux = u8;
}
#[doc = "`write(|w| ..)` method takes [`clear::W`](W) writer structure"]
impl crate::Writable for ClearSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets CLEAR to value 0"]
impl crate::Resettable for ClearSpec {}
