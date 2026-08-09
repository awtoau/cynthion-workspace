#[doc = "Register `PRIORITY6` reader"]
pub type R = crate::R<Priority6Spec>;
#[doc = "Register `PRIORITY6` writer"]
pub type W = crate::W<Priority6Spec>;
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
    pub fn level(&mut self) -> LevelW<'_, Priority6Spec> {
        LevelW::new(self, 0)
    }
}
#[doc = "PLIC.PRIORITY6, 3 bits at +0x18\n\nYou can [`read`](crate::Reg::read) this register and get [`priority6::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`priority6::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Priority6Spec;
impl crate::RegisterSpec for Priority6Spec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`priority6::R`](R) reader structure"]
impl crate::Readable for Priority6Spec {}
#[doc = "`write(|w| ..)` method takes [`priority6::W`](W) writer structure"]
impl crate::Writable for Priority6Spec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets PRIORITY6 to value 0"]
impl crate::Resettable for Priority6Spec {}
