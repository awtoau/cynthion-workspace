#[doc = "Register `STREAM_CTRL` reader"]
pub type R = crate::R<StreamCtrlSpec>;
#[doc = "Register `STREAM_CTRL` writer"]
pub type W = crate::W<StreamCtrlSpec>;
#[doc = "Field `V` reader - v \\[0\\]"]
pub type VR = crate::BitReader;
#[doc = "Field `V` writer - v \\[0\\]"]
pub type VW<'a, REG> = crate::BitWriter<'a, REG>;
impl R {
    #[doc = "Bit 0 - v \\[0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new((self.bits & 1) != 0)
    }
}
impl W {
    #[doc = "Bit 0 - v \\[0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, StreamCtrlSpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.STREAM_CTRL, 1 bits at +0x78\n\nYou can [`read`](crate::Reg::read) this register and get [`stream_ctrl::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`stream_ctrl::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StreamCtrlSpec;
impl crate::RegisterSpec for StreamCtrlSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`stream_ctrl::R`](R) reader structure"]
impl crate::Readable for StreamCtrlSpec {}
#[doc = "`write(|w| ..)` method takes [`stream_ctrl::W`](W) writer structure"]
impl crate::Writable for StreamCtrlSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets STREAM_CTRL to value 0"]
impl crate::Resettable for StreamCtrlSpec {}
