#[doc = "Register `P05` reader"]
pub type R = crate::R<P05Spec>;
#[doc = "Register `P05` writer"]
pub type W = crate::W<P05Spec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
#[doc = "Field `W` writer - w \\[31:0\\]"]
pub type WW<'a, REG> = crate::FieldWriter<'a, REG, 32, u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
impl W {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&mut self) -> WW<'_, P05Spec> {
        WW::new(self, 0)
    }
}
#[doc = "HYPERRAM_BIST.P05, 32 bits at +0x14\n\nYou can [`read`](crate::Reg::read) this register and get [`p05::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p05::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct P05Spec;
impl crate::RegisterSpec for P05Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`p05::R`](R) reader structure"]
impl crate::Readable for P05Spec {}
#[doc = "`write(|w| ..)` method takes [`p05::W`](W) writer structure"]
impl crate::Writable for P05Spec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets P05 to value 0"]
impl crate::Resettable for P05Spec {}
