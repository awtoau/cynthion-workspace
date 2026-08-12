#[doc = "Register `P1D` reader"]
pub type R = crate::R<P1dSpec>;
#[doc = "Register `P1D` writer"]
pub type W = crate::W<P1dSpec>;
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
    pub fn w(&mut self) -> WW<'_, P1dSpec> {
        WW::new(self, 0)
    }
}
#[doc = "HYPERRAM_BIST.P1D, 32 bits at +0x74\n\nYou can [`read`](crate::Reg::read) this register and get [`p1d::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p1d::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct P1dSpec;
impl crate::RegisterSpec for P1dSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`p1d::R`](R) reader structure"]
impl crate::Readable for P1dSpec {}
#[doc = "`write(|w| ..)` method takes [`p1d::W`](W) writer structure"]
impl crate::Writable for P1dSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets P1D to value 0"]
impl crate::Resettable for P1dSpec {}
