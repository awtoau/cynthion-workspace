#[doc = "Register `FILL_VALUE` reader"]
pub type R = crate::R<FillValueSpec>;
#[doc = "Register `FILL_VALUE` writer"]
pub type W = crate::W<FillValueSpec>;
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
    pub fn v(&mut self) -> VW<'_, FillValueSpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.FILL_VALUE, 32 bits at +0x20\n\nYou can [`read`](crate::Reg::read) this register and get [`fill_value::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`fill_value::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct FillValueSpec;
impl crate::RegisterSpec for FillValueSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`fill_value::R`](R) reader structure"]
impl crate::Readable for FillValueSpec {}
#[doc = "`write(|w| ..)` method takes [`fill_value::W`](W) writer structure"]
impl crate::Writable for FillValueSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets FILL_VALUE to value 0"]
impl crate::Resettable for FillValueSpec {}
