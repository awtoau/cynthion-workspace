#[doc = "Register `SEL` writer"]
pub type W = crate::W<SelSpec>;
#[doc = "Field `TAP` writer - tap \\[3:0\\]"]
pub type TapW<'a, REG> = crate::FieldWriter<'a, REG, 4>;
impl W {
    #[doc = "Bits 0:3 - tap \\[3:0\\]"]
    #[inline(always)]
    pub fn tap(&mut self) -> TapW<'_, SelSpec> {
        TapW::new(self, 0)
    }
}
#[doc = "HYPERRAM_PROBE.SEL, 4 bits at +0x21\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`sel::W`](W). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct SelSpec;
impl crate::RegisterSpec for SelSpec {
    type Ux = u8;
}
#[doc = "`write(|w| ..)` method takes [`sel::W`](W) writer structure"]
impl crate::Writable for SelSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets SEL to value 0"]
impl crate::Resettable for SelSpec {}
