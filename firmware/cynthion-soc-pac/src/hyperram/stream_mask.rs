#[doc = "Register `STREAM_MASK` reader"]
pub type R = crate::R<StreamMaskSpec>;
#[doc = "Register `STREAM_MASK` writer"]
pub type W = crate::W<StreamMaskSpec>;
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
    pub fn v(&mut self) -> VW<'_, StreamMaskSpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.STREAM_MASK, 22 bits at +0x74\n\nYou can [`read`](crate::Reg::read) this register and get [`stream_mask::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`stream_mask::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StreamMaskSpec;
impl crate::RegisterSpec for StreamMaskSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`stream_mask::R`](R) reader structure"]
impl crate::Readable for StreamMaskSpec {}
#[doc = "`write(|w| ..)` method takes [`stream_mask::W`](W) writer structure"]
impl crate::Writable for StreamMaskSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets STREAM_MASK to value 0x000f_ffff"]
impl crate::Resettable for StreamMaskSpec {
    const RESET_VALUE: u32 = 0x000f_ffff;
}
