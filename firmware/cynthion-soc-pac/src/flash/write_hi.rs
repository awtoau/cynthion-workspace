#[doc = "Register `WRITE_HI` reader"]
pub type R = crate::R<WriteHiSpec>;
#[doc = "Register `WRITE_HI` writer"]
pub type W = crate::W<WriteHiSpec>;
#[doc = "Field `V` reader - v \\[10:0\\]"]
pub type VR = crate::FieldReader<u16>;
#[doc = "Field `V` writer - v \\[10:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 11, u16>;
impl R {
    #[doc = "Bits 0:10 - v \\[10:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 0x07ff)
    }
}
impl W {
    #[doc = "Bits 0:10 - v \\[10:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, WriteHiSpec> {
        VW::new(self, 0)
    }
}
#[doc = "FLASH.WRITE_HI, 11 bits at +0x22\n\nYou can [`read`](crate::Reg::read) this register and get [`write_hi::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`write_hi::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct WriteHiSpec;
impl crate::RegisterSpec for WriteHiSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`write_hi::R`](R) reader structure"]
impl crate::Readable for WriteHiSpec {}
#[doc = "`write(|w| ..)` method takes [`write_hi::W`](W) writer structure"]
impl crate::Writable for WriteHiSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets WRITE_HI to value 0"]
impl crate::Resettable for WriteHiSpec {}
