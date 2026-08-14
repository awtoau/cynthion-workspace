#[doc = "Register `RWDS_AT` writer"]
pub type W = crate::W<RwdsAtSpec>;
#[doc = "Field `CYCLE` writer - cycle \\[3:0\\]"]
pub type CycleW<'a, REG> = crate::FieldWriter<'a, REG, 4>;
impl W {
    #[doc = "Bits 0:3 - cycle \\[3:0\\]"]
    #[inline(always)]
    pub fn cycle(&mut self) -> CycleW<'_, RwdsAtSpec> {
        CycleW::new(self, 0)
    }
}
#[doc = "HYPERRAM_PROBE.RWDS_AT, 4 bits at +0x26\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`rwds_at::W`](W). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RwdsAtSpec;
impl crate::RegisterSpec for RwdsAtSpec {
    type Ux = u8;
}
#[doc = "`write(|w| ..)` method takes [`rwds_at::W`](W) writer structure"]
impl crate::Writable for RwdsAtSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets RWDS_AT to value 0"]
impl crate::Resettable for RwdsAtSpec {}
