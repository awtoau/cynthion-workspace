#[doc = "Register `TR_SEL` reader"]
pub type R = crate::R<TrSelSpec>;
#[doc = "Register `TR_SEL` writer"]
pub type W = crate::W<TrSelSpec>;
#[doc = "Field `V` reader - v \\[2:0\\]"]
pub type VR = crate::FieldReader;
#[doc = "Field `V` writer - v \\[2:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 3>;
impl R {
    #[doc = "Bits 0:2 - v \\[2:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 7)
    }
}
impl W {
    #[doc = "Bits 0:2 - v \\[2:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, TrSelSpec> {
        VW::new(self, 0)
    }
}
#[doc = "USB_HOST.TR_SEL, 3 bits at +0x1e\n\nYou can [`read`](crate::Reg::read) this register and get [`tr_sel::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tr_sel::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct TrSelSpec;
impl crate::RegisterSpec for TrSelSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`tr_sel::R`](R) reader structure"]
impl crate::Readable for TrSelSpec {}
#[doc = "`write(|w| ..)` method takes [`tr_sel::W`](W) writer structure"]
impl crate::Writable for TrSelSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets TR_SEL to value 0"]
impl crate::Resettable for TrSelSpec {}
