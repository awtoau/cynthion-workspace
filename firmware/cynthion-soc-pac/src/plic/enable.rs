#[doc = "Register `ENABLE` reader"]
pub type R = crate::R<EnableSpec>;
#[doc = "Register `ENABLE` writer"]
pub type W = crate::W<EnableSpec>;
#[doc = "Field `BITS` reader - bits \\[4:0\\]"]
pub type BitsR = crate::FieldReader;
#[doc = "Field `BITS` writer - bits \\[4:0\\]"]
pub type BitsW<'a, REG> = crate::FieldWriter<'a, REG, 5>;
impl R {
    #[doc = "Bits 0:4 - bits \\[4:0\\]"]
    #[inline(always)]
    pub fn bits_(&self) -> BitsR {
        BitsR::new(self.bits & 0x1f)
    }
}
impl W {
    #[doc = "Bits 0:4 - bits \\[4:0\\]"]
    #[inline(always)]
    pub fn bits_(&mut self) -> BitsW<'_, EnableSpec> {
        BitsW::new(self, 0)
    }
}
#[doc = "PLIC.ENABLE, 5 bits at +0x2000\n\nYou can [`read`](crate::Reg::read) this register and get [`enable::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`enable::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct EnableSpec;
impl crate::RegisterSpec for EnableSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`enable::R`](R) reader structure"]
impl crate::Readable for EnableSpec {}
#[doc = "`write(|w| ..)` method takes [`enable::W`](W) writer structure"]
impl crate::Writable for EnableSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets ENABLE to value 0"]
impl crate::Resettable for EnableSpec {}
