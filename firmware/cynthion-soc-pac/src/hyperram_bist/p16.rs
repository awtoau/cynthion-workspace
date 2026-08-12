#[doc = "Register `P16` reader"]
pub type R = crate::R<P16Spec>;
#[doc = "Register `P16` writer"]
pub type W = crate::W<P16Spec>;
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
    pub fn w(&mut self) -> WW<'_, P16Spec> {
        WW::new(self, 0)
    }
}
#[doc = "HYPERRAM_BIST.P16, 32 bits at +0x58\n\nYou can [`read`](crate::Reg::read) this register and get [`p16::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p16::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct P16Spec;
impl crate::RegisterSpec for P16Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`p16::R`](R) reader structure"]
impl crate::Readable for P16Spec {}
#[doc = "`write(|w| ..)` method takes [`p16::W`](W) writer structure"]
impl crate::Writable for P16Spec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets P16 to value 0"]
impl crate::Resettable for P16Spec {}
