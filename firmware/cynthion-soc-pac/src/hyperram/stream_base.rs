#[doc = "Register `STREAM_BASE` reader"]
pub type R = crate::R<StreamBaseSpec>;
#[doc = "Register `STREAM_BASE` writer"]
pub type W = crate::W<StreamBaseSpec>;
#[doc = "Field `V` reader - v \\[21:0\\]"]
pub type VR = crate::FieldReader<u32>;
#[doc = "Field `V` writer - v \\[21:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 22, u32>;
impl R {
    #[doc = "Bits 0:21 - v \\[21:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 0x003f_ffff)
    }
}
impl W {
    #[doc = "Bits 0:21 - v \\[21:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, StreamBaseSpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.STREAM_BASE, 22 bits at +0x70\n\nYou can [`read`](crate::Reg::read) this register and get [`stream_base::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`stream_base::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StreamBaseSpec;
impl crate::RegisterSpec for StreamBaseSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`stream_base::R`](R) reader structure"]
impl crate::Readable for StreamBaseSpec {}
#[doc = "`write(|w| ..)` method takes [`stream_base::W`](W) writer structure"]
impl crate::Writable for StreamBaseSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets STREAM_BASE to value 0"]
impl crate::Resettable for StreamBaseSpec {}
