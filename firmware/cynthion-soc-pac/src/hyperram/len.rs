#[doc = "Register `LEN` reader"]
pub type R = crate::R<LenSpec>;
#[doc = "Register `LEN` writer"]
pub type W = crate::W<LenSpec>;
#[doc = "Field `V` reader - v \\[23:0\\]"]
pub type VR = crate::FieldReader<u32>;
#[doc = "Field `V` writer - v \\[23:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 24, u32>;
impl R {
    #[doc = "Bits 0:23 - v \\[23:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 0x00ff_ffff)
    }
}
impl W {
    #[doc = "Bits 0:23 - v \\[23:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, LenSpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.LEN, 24 bits at +0x1c\n\nYou can [`read`](crate::Reg::read) this register and get [`len::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`len::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct LenSpec;
impl crate::RegisterSpec for LenSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`len::R`](R) reader structure"]
impl crate::Readable for LenSpec {}
#[doc = "`write(|w| ..)` method takes [`len::W`](W) writer structure"]
impl crate::Writable for LenSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets LEN to value 0"]
impl crate::Resettable for LenSpec {}
