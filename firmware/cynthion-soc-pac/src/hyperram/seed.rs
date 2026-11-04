#[doc = "Register `SEED` reader"]
pub type R = crate::R<SeedSpec>;
#[doc = "Register `SEED` writer"]
pub type W = crate::W<SeedSpec>;
#[doc = "Field `V` reader - v \\[15:0\\]"]
pub type VR = crate::FieldReader<u16>;
#[doc = "Field `V` writer - v \\[15:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 16, u16>;
impl R {
    #[doc = "Bits 0:15 - v \\[15:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
impl W {
    #[doc = "Bits 0:15 - v \\[15:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, SeedSpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.SEED, 16 bits at +0x28\n\nYou can [`read`](crate::Reg::read) this register and get [`seed::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`seed::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct SeedSpec;
impl crate::RegisterSpec for SeedSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`seed::R`](R) reader structure"]
impl crate::Readable for SeedSpec {}
#[doc = "`write(|w| ..)` method takes [`seed::W`](W) writer structure"]
impl crate::Writable for SeedSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets SEED to value 0"]
impl crate::Resettable for SeedSpec {}
