#[doc = "Register `DELAY` reader"]
pub type R = crate::R<DelaySpec>;
#[doc = "Register `DELAY` writer"]
pub type W = crate::W<DelaySpec>;
#[doc = "Field `V` reader - v \\[6:0\\]"]
pub type VR = crate::FieldReader;
#[doc = "Field `V` writer - v \\[6:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 7>;
impl R {
    #[doc = "Bits 0:6 - v \\[6:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 0x7f)
    }
}
impl W {
    #[doc = "Bits 0:6 - v \\[6:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, DelaySpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.DELAY, 7 bits at +0x27\n\nYou can [`read`](crate::Reg::read) this register and get [`delay::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`delay::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct DelaySpec;
impl crate::RegisterSpec for DelaySpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`delay::R`](R) reader structure"]
impl crate::Readable for DelaySpec {}
#[doc = "`write(|w| ..)` method takes [`delay::W`](W) writer structure"]
impl crate::Writable for DelaySpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets DELAY to value 0"]
impl crate::Resettable for DelaySpec {}
