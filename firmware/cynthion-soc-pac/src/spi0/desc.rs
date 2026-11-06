#[doc = "Register `DESC` reader"]
pub type R = crate::R<DescSpec>;
#[doc = "Register `DESC` writer"]
pub type W = crate::W<DescSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
#[doc = "Field `V` writer - v \\[31:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 32, u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
impl W {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, DescSpec> {
        VW::new(self, 0)
    }
}
#[doc = "SPI0.DESC, 32 bits at +0x14\n\nYou can [`read`](crate::Reg::read) this register and get [`desc::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`desc::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct DescSpec;
impl crate::RegisterSpec for DescSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`desc::R`](R) reader structure"]
impl crate::Readable for DescSpec {}
#[doc = "`write(|w| ..)` method takes [`desc::W`](W) writer structure"]
impl crate::Writable for DescSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets DESC to value 0"]
impl crate::Resettable for DescSpec {}
