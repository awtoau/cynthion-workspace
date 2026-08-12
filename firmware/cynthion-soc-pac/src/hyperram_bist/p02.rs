#[doc = "Register `P02` reader"]
pub type R = crate::R<P02Spec>;
#[doc = "Register `P02` writer"]
pub type W = crate::W<P02Spec>;
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
    pub fn w(&mut self) -> WW<'_, P02Spec> {
        WW::new(self, 0)
    }
}
#[doc = "HYPERRAM_BIST.P02, 32 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`p02::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p02::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct P02Spec;
impl crate::RegisterSpec for P02Spec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`p02::R`](R) reader structure"]
impl crate::Readable for P02Spec {}
#[doc = "`write(|w| ..)` method takes [`p02::W`](W) writer structure"]
impl crate::Writable for P02Spec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets P02 to value 0"]
impl crate::Resettable for P02Spec {}
