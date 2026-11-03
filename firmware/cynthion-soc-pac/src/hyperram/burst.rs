#[doc = "Register `BURST` reader"]
pub type R = crate::R<BurstSpec>;
#[doc = "Register `BURST` writer"]
pub type W = crate::W<BurstSpec>;
#[doc = "Field `V` reader - v \\[15:0\\]"]
pub type VR = crate::FieldReader<u16>;
#[doc = "Field `V` writer - v \\[15:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 16, u16>;
impl R {
    #[doc = "Bits 0:15 - v \\[15:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
impl W {
    #[doc = "Bits 0:15 - v \\[15:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, BurstSpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.BURST, 16 bits at +0x2c\n\nYou can [`read`](crate::Reg::read) this register and get [`burst::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`burst::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct BurstSpec;
impl crate::RegisterSpec for BurstSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`burst::R`](R) reader structure"]
impl crate::Readable for BurstSpec {}
#[doc = "`write(|w| ..)` method takes [`burst::W`](W) writer structure"]
impl crate::Writable for BurstSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets BURST to value 0"]
impl crate::Resettable for BurstSpec {}
