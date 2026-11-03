#[doc = "Register `ERRORS` reader"]
pub type R = crate::R<ErrorsSpec>;
#[doc = "Register `ERRORS` writer"]
pub type W = crate::W<ErrorsSpec>;
#[doc = "Field `V` reader - v \\[12:0\\]"]
pub type VR = crate::FieldReader<u16>;
#[doc = "Field `V` writer - v \\[12:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 13, u16>;
impl R {
    #[doc = "Bits 0:12 - v \\[12:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 0x1fff)
    }
}
impl W {
    #[doc = "Bits 0:12 - v \\[12:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, ErrorsSpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.ERRORS, 13 bits at +0x10\n\nYou can [`read`](crate::Reg::read) this register and get [`errors::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`errors::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct ErrorsSpec;
impl crate::RegisterSpec for ErrorsSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`errors::R`](R) reader structure"]
impl crate::Readable for ErrorsSpec {}
#[doc = "`write(|w| ..)` method takes [`errors::W`](W) writer structure"]
impl crate::Writable for ErrorsSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets ERRORS to value 0"]
impl crate::Resettable for ErrorsSpec {}
