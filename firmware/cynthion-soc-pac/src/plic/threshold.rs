#[doc = "Register `THRESHOLD` reader"]
pub type R = crate::R<ThresholdSpec>;
#[doc = "Register `THRESHOLD` writer"]
pub type W = crate::W<ThresholdSpec>;
#[doc = "Field `LEVEL` reader - level \\[2:0\\]"]
pub type LevelR = crate::FieldReader;
#[doc = "Field `LEVEL` writer - level \\[2:0\\]"]
pub type LevelW<'a, REG> = crate::FieldWriter<'a, REG, 3>;
impl R {
    #[doc = "Bits 0:2 - level \\[2:0\\]"]
    #[inline(always)]
    pub fn level(&self) -> LevelR {
        LevelR::new(self.bits & 7)
    }
}
impl W {
    #[doc = "Bits 0:2 - level \\[2:0\\]"]
    #[inline(always)]
    pub fn level(&mut self) -> LevelW<'_, ThresholdSpec> {
        LevelW::new(self, 0)
    }
}
#[doc = "PLIC.THRESHOLD, 3 bits at +0x200000\n\nYou can [`read`](crate::Reg::read) this register and get [`threshold::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`threshold::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct ThresholdSpec;
impl crate::RegisterSpec for ThresholdSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`threshold::R`](R) reader structure"]
impl crate::Readable for ThresholdSpec {}
#[doc = "`write(|w| ..)` method takes [`threshold::W`](W) writer structure"]
impl crate::Writable for ThresholdSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets THRESHOLD to value 0"]
impl crate::Resettable for ThresholdSpec {}
