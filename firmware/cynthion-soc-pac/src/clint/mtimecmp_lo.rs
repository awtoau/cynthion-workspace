#[doc = "Register `MTIMECMP_LO` reader"]
pub type R = crate::R<MtimecmpLoSpec>;
#[doc = "Register `MTIMECMP_LO` writer"]
pub type W = crate::W<MtimecmpLoSpec>;
#[doc = "Field `VALUE` reader - value \\[31:0\\]"]
pub type ValueR = crate::FieldReader<u32>;
#[doc = "Field `VALUE` writer - value \\[31:0\\]"]
pub type ValueW<'a, REG> = crate::FieldWriter<'a, REG, 32, u32>;
impl R {
    #[doc = "Bits 0:31 - value \\[31:0\\]"]
    #[inline(always)]
    pub fn value(&self) -> ValueR {
        ValueR::new(self.bits)
    }
}
impl W {
    #[doc = "Bits 0:31 - value \\[31:0\\]"]
    #[inline(always)]
    pub fn value(&mut self) -> ValueW<'_, MtimecmpLoSpec> {
        ValueW::new(self, 0)
    }
}
#[doc = "CLINT.MTIMECMP_LO, 32 bits at +0x4000\n\nYou can [`read`](crate::Reg::read) this register and get [`mtimecmp_lo::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`mtimecmp_lo::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct MtimecmpLoSpec;
impl crate::RegisterSpec for MtimecmpLoSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`mtimecmp_lo::R`](R) reader structure"]
impl crate::Readable for MtimecmpLoSpec {}
#[doc = "`write(|w| ..)` method takes [`mtimecmp_lo::W`](W) writer structure"]
impl crate::Writable for MtimecmpLoSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets MTIMECMP_LO to value 0xffff_ffff"]
impl crate::Resettable for MtimecmpLoSpec {
    const RESET_VALUE: u32 = 0xffff_ffff;
}
