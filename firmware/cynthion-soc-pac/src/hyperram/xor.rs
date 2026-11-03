#[doc = "Register `XOR` reader"]
pub type R = crate::R<XorSpec>;
#[doc = "Register `XOR` writer"]
pub type W = crate::W<XorSpec>;
#[doc = "Field `V` reader - v \\[15:0\\]"]
pub type VR = crate::FieldReader<u16>;
#[doc = "Field `V` writer - v \\[15:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 16, u16>;
impl R {
    #[doc = "Bits 0:15 - v \\[15:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
impl W {
    #[doc = "Bits 0:15 - v \\[15:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, XorSpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.XOR, 16 bits at +0x2a\n\nYou can [`read`](crate::Reg::read) this register and get [`xor::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`xor::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct XorSpec;
impl crate::RegisterSpec for XorSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`xor::R`](R) reader structure"]
impl crate::Readable for XorSpec {}
#[doc = "`write(|w| ..)` method takes [`xor::W`](W) writer structure"]
impl crate::Writable for XorSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets XOR to value 0"]
impl crate::Resettable for XorSpec {}
