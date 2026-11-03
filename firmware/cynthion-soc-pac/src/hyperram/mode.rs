#[doc = "Register `MODE` reader"]
pub type R = crate::R<ModeSpec>;
#[doc = "Register `MODE` writer"]
pub type W = crate::W<ModeSpec>;
#[doc = "Field `FILL` reader - fill \\[1:0\\]"]
pub type FillR = crate::FieldReader;
#[doc = "Field `FILL` writer - fill \\[1:0\\]"]
pub type FillW<'a, REG> = crate::FieldWriter<'a, REG, 2>;
#[doc = "Field `TEST` reader - test \\[3:2\\]"]
pub type TestR = crate::FieldReader;
#[doc = "Field `TEST` writer - test \\[3:2\\]"]
pub type TestW<'a, REG> = crate::FieldWriter<'a, REG, 2>;
#[doc = "Field `INJECT_HANG` reader - inject_hang \\[4\\]"]
pub type InjectHangR = crate::BitReader;
#[doc = "Field `INJECT_HANG` writer - inject_hang \\[4\\]"]
pub type InjectHangW<'a, REG> = crate::BitWriter<'a, REG>;
impl R {
    #[doc = "Bits 0:1 - fill \\[1:0\\]"]
    #[inline(always)]
    pub fn fill(&self) -> FillR {
        FillR::new(self.bits & 3)
    }
    #[doc = "Bits 2:3 - test \\[3:2\\]"]
    #[inline(always)]
    pub fn test(&self) -> TestR {
        TestR::new((self.bits >> 2) & 3)
    }
    #[doc = "Bit 4 - inject_hang \\[4\\]"]
    #[inline(always)]
    pub fn inject_hang(&self) -> InjectHangR {
        InjectHangR::new(((self.bits >> 4) & 1) != 0)
    }
}
impl W {
    #[doc = "Bits 0:1 - fill \\[1:0\\]"]
    #[inline(always)]
    pub fn fill(&mut self) -> FillW<'_, ModeSpec> {
        FillW::new(self, 0)
    }
    #[doc = "Bits 2:3 - test \\[3:2\\]"]
    #[inline(always)]
    pub fn test(&mut self) -> TestW<'_, ModeSpec> {
        TestW::new(self, 2)
    }
    #[doc = "Bit 4 - inject_hang \\[4\\]"]
    #[inline(always)]
    pub fn inject_hang(&mut self) -> InjectHangW<'_, ModeSpec> {
        InjectHangW::new(self, 4)
    }
}
#[doc = "HYPERRAM.MODE, 5 bits at +0x24\n\nYou can [`read`](crate::Reg::read) this register and get [`mode::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`mode::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct ModeSpec;
impl crate::RegisterSpec for ModeSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`mode::R`](R) reader structure"]
impl crate::Readable for ModeSpec {}
#[doc = "`write(|w| ..)` method takes [`mode::W`](W) writer structure"]
impl crate::Writable for ModeSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets MODE to value 0"]
impl crate::Resettable for ModeSpec {}
