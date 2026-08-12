#[doc = "Register `P18` reader"]
pub type R = crate::R<P18Spec>;
#[doc = "Register `P18` writer"]
pub type W = crate::W<P18Spec>;
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
    pub fn w(&mut self) -> WW<'_, P18Spec> {
        WW::new(self, 0)
    }
}
#[doc = "HYPERRAM_BIST.P18, 32 bits at +0x60\n\nYou can [`read`](crate::Reg::read) this register and get [`p18::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p18::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct P18Spec;
impl crate::RegisterSpec for P18Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`p18::R`](R) reader structure"]
impl crate::Readable for P18Spec {}
#[doc = "`write(|w| ..)` method takes [`p18::W`](W) writer structure"]
impl crate::Writable for P18Spec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets P18 to value 0"]
impl crate::Resettable for P18Spec {}
