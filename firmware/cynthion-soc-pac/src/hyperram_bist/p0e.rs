#[doc = "Register `P0E` reader"]
pub type R = crate::R<P0eSpec>;
#[doc = "Register `P0E` writer"]
pub type W = crate::W<P0eSpec>;
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
    pub fn w(&mut self) -> WW<'_, P0eSpec> {
        WW::new(self, 0)
    }
}
#[doc = "HYPERRAM_BIST.P0E, 32 bits at +0x38\n\nYou can [`read`](crate::Reg::read) this register and get [`p0e::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p0e::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct P0eSpec;
impl crate::RegisterSpec for P0eSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`p0e::R`](R) reader structure"]
impl crate::Readable for P0eSpec {}
#[doc = "`write(|w| ..)` method takes [`p0e::W`](W) writer structure"]
impl crate::Writable for P0eSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets P0E to value 0"]
impl crate::Resettable for P0eSpec {}
