#[doc = "Register `P0B` reader"]
pub type R = crate::R<P0bSpec>;
#[doc = "Register `P0B` writer"]
pub type W = crate::W<P0bSpec>;
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
    pub fn w(&mut self) -> WW<'_, P0bSpec> {
        WW::new(self, 0)
    }
}
#[doc = "HYPERRAM_BIST.P0B, 32 bits at +0x2c\n\nYou can [`read`](crate::Reg::read) this register and get [`p0b::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p0b::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct P0bSpec;
impl crate::RegisterSpec for P0bSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`p0b::R`](R) reader structure"]
impl crate::Readable for P0bSpec {}
#[doc = "`write(|w| ..)` method takes [`p0b::W`](W) writer structure"]
impl crate::Writable for P0bSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets P0B to value 0"]
impl crate::Resettable for P0bSpec {}
