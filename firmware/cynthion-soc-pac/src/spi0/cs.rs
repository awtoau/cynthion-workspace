#[doc = "Register `CS` writer"]
pub type W = crate::W<CsSpec>;
#[doc = "Field `SELECT` writer - select \\[0\\]"]
pub type SelectW<'a, REG> = crate::BitWriter<'a, REG>;
impl W {
    #[doc = "Bit 0 - select \\[0\\]"]
    #[inline(always)]
    pub fn select(&mut self) -> SelectW<'_, CsSpec> {
        SelectW::new(self, 0)
    }
}
#[doc = "SPI0.CS, 1 bits at +0x04\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`cs::W`](W). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CsSpec;
impl crate::RegisterSpec for CsSpec {
    type Ux = u8;
}
#[doc = "`write(|w| ..)` method takes [`cs::W`](W) writer structure"]
impl crate::Writable for CsSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets CS to value 0"]
impl crate::Resettable for CsSpec {}
