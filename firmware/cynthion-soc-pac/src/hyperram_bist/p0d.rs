#[doc = "Register `P0D` reader"]
pub type R = crate::R<P0dSpec>;
#[doc = "Register `P0D` writer"]
pub type W = crate::W<P0dSpec>;
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
    pub fn w(&mut self) -> WW<'_, P0dSpec> {
        WW::new(self, 0)
    }
}
#[doc = "HYPERRAM_BIST.P0D, 32 bits at +0x34\n\nYou can [`read`](crate::Reg::read) this register and get [`p0d::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p0d::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct P0dSpec;
impl crate::RegisterSpec for P0dSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`p0d::R`](R) reader structure"]
impl crate::Readable for P0dSpec {}
#[doc = "`write(|w| ..)` method takes [`p0d::W`](W) writer structure"]
impl crate::Writable for P0dSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets P0D to value 0"]
impl crate::Resettable for P0dSpec {}
