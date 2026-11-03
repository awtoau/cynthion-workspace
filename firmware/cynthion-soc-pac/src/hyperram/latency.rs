#[doc = "Register `LATENCY` reader"]
pub type R = crate::R<LatencySpec>;
#[doc = "Register `LATENCY` writer"]
pub type W = crate::W<LatencySpec>;
#[doc = "Field `V` reader - v \\[4:0\\]"]
pub type VR = crate::FieldReader;
#[doc = "Field `V` writer - v \\[4:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 5>;
impl R {
    #[doc = "Bits 0:4 - v \\[4:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 0x1f)
    }
}
impl W {
    #[doc = "Bits 0:4 - v \\[4:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, LatencySpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.LATENCY, 5 bits at +0x26\n\nYou can [`read`](crate::Reg::read) this register and get [`latency::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`latency::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct LatencySpec;
impl crate::RegisterSpec for LatencySpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`latency::R`](R) reader structure"]
impl crate::Readable for LatencySpec {}
#[doc = "`write(|w| ..)` method takes [`latency::W`](W) writer structure"]
impl crate::Writable for LatencySpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets LATENCY to value 0x0d"]
impl crate::Resettable for LatencySpec {
    const RESET_VALUE: u8 = 0x0d;
}
