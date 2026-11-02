#[doc = "Register `EP` reader"]
pub type R = crate::R<EpSpec>;
#[doc = "Register `EP` writer"]
pub type W = crate::W<EpSpec>;
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
    pub fn v(&mut self) -> VW<'_, EpSpec> {
        VW::new(self, 0)
    }
}
#[doc = "USB_HOST.EP, 7 bits at +0x09\n\nYou can [`read`](crate::Reg::read) this register and get [`ep::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ep::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct EpSpec;
impl crate::RegisterSpec for EpSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`ep::R`](R) reader structure"]
impl crate::Readable for EpSpec {}
#[doc = "`write(|w| ..)` method takes [`ep::W`](W) writer structure"]
impl crate::Writable for EpSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets EP to value 0"]
impl crate::Resettable for EpSpec {}
