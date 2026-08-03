#[doc = "Register `CLAIM` reader"]
pub type R = crate::R<ClaimSpec>;
#[doc = "Register `CLAIM` writer"]
pub type W = crate::W<ClaimSpec>;
#[doc = "Field `SOURCE` reader - source \\[7:0\\]"]
pub type SourceR = crate::FieldReader;
#[doc = "Field `SOURCE` writer - source \\[7:0\\]"]
pub type SourceW<'a, REG> = crate::FieldWriter<'a, REG, 8>;
impl R {
    #[doc = "Bits 0:7 - source \\[7:0\\]"]
    #[inline(always)]
    pub fn source(&self) -> SourceR {
        SourceR::new(self.bits)
    }
}
impl W {
    #[doc = "Bits 0:7 - source \\[7:0\\]"]
    #[inline(always)]
    pub fn source(&mut self) -> SourceW<'_, ClaimSpec> {
        SourceW::new(self, 0)
    }
}
#[doc = "PLIC.CLAIM, 8 bits at +0x200004\n\nYou can [`read`](crate::Reg::read) this register and get [`claim::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`claim::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct ClaimSpec;
impl crate::RegisterSpec for ClaimSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`claim::R`](R) reader structure"]
impl crate::Readable for ClaimSpec {}
#[doc = "`write(|w| ..)` method takes [`claim::W`](W) writer structure"]
impl crate::Writable for ClaimSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets CLAIM to value 0"]
impl crate::Resettable for ClaimSpec {}
