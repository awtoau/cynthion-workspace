#[doc = "Register `MTIMECMP_HI` reader"]
pub type R = crate::R<MtimecmpHiSpec>;
#[doc = "Register `MTIMECMP_HI` writer"]
pub type W = crate::W<MtimecmpHiSpec>;
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
    pub fn value(&mut self) -> ValueW<'_, MtimecmpHiSpec> {
        ValueW::new(self, 0)
    }
}
#[doc = "CLINT.MTIMECMP_HI, 32 bits at +0x4004\n\nYou can [`read`](crate::Reg::read) this register and get [`mtimecmp_hi::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`mtimecmp_hi::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct MtimecmpHiSpec;
impl crate::RegisterSpec for MtimecmpHiSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`mtimecmp_hi::R`](R) reader structure"]
impl crate::Readable for MtimecmpHiSpec {}
#[doc = "`write(|w| ..)` method takes [`mtimecmp_hi::W`](W) writer structure"]
impl crate::Writable for MtimecmpHiSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets MTIMECMP_HI to value 0xffff_ffff"]
impl crate::Resettable for MtimecmpHiSpec {
    const RESET_VALUE: u32 = 0xffff_ffff;
}
