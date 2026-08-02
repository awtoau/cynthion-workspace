#[doc = "Register `PRER_LO` reader"]
pub type R = crate::R<PrerLoSpec>;
#[doc = "Register `PRER_LO` writer"]
pub type W = crate::W<PrerLoSpec>;
#[doc = "Field `DATA` reader - data \\[7:0\\]"]
pub type DataR = crate::FieldReader;
#[doc = "Field `DATA` writer - data \\[7:0\\]"]
pub type DataW<'a, REG> = crate::FieldWriter<'a, REG, 8>;
impl R {
    #[doc = "Bits 0:7 - data \\[7:0\\]"]
    #[inline(always)]
    pub fn data(&self) -> DataR {
        DataR::new(self.bits)
    }
}
impl W {
    #[doc = "Bits 0:7 - data \\[7:0\\]"]
    #[inline(always)]
    pub fn data(&mut self) -> DataW<'_, PrerLoSpec> {
        DataW::new(self, 0)
    }
}
#[doc = "BOARD_I2C.PRER_LO, 8 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`prer_lo::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`prer_lo::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct PrerLoSpec;
impl crate::RegisterSpec for PrerLoSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`prer_lo::R`](R) reader structure"]
impl crate::Readable for PrerLoSpec {}
#[doc = "`write(|w| ..)` method takes [`prer_lo::W`](W) writer structure"]
impl crate::Writable for PrerLoSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets PRER_LO to value 0"]
impl crate::Resettable for PrerLoSpec {}
