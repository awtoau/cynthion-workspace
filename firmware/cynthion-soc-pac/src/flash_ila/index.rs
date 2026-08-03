#[doc = "Register `INDEX` reader"]
pub type R = crate::R<IndexSpec>;
#[doc = "Register `INDEX` writer"]
pub type W = crate::W<IndexSpec>;
#[doc = "Field `VALUE` reader - value \\[15:0\\]"]
pub type ValueR = crate::FieldReader<u16>;
#[doc = "Field `VALUE` writer - value \\[15:0\\]"]
pub type ValueW<'a, REG> = crate::FieldWriter<'a, REG, 16, u16>;
impl R {
    #[doc = "Bits 0:15 - value \\[15:0\\]"]
    #[inline(always)]
    pub fn value(&self) -> ValueR {
        ValueR::new(self.bits)
    }
}
impl W {
    #[doc = "Bits 0:15 - value \\[15:0\\]"]
    #[inline(always)]
    pub fn value(&mut self) -> ValueW<'_, IndexSpec> {
        ValueW::new(self, 0)
    }
}
#[doc = "FLASH_ILA.INDEX, 16 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`index::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`index::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct IndexSpec;
impl crate::RegisterSpec for IndexSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`index::R`](R) reader structure"]
impl crate::Readable for IndexSpec {}
#[doc = "`write(|w| ..)` method takes [`index::W`](W) writer structure"]
impl crate::Writable for IndexSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets INDEX to value 0"]
impl crate::Resettable for IndexSpec {}
