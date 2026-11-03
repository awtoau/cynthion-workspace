#[doc = "Register `REG_SEL` reader"]
pub type R = crate::R<RegSelSpec>;
#[doc = "Register `REG_SEL` writer"]
pub type W = crate::W<RegSelSpec>;
#[doc = "Field `V` reader - v \\[1:0\\]"]
pub type VR = crate::FieldReader;
#[doc = "Field `V` writer - v \\[1:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 2>;
impl R {
    #[doc = "Bits 0:1 - v \\[1:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 3)
    }
}
impl W {
    #[doc = "Bits 0:1 - v \\[1:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, RegSelSpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.REG_SEL, 2 bits at +0x25\n\nYou can [`read`](crate::Reg::read) this register and get [`reg_sel::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`reg_sel::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RegSelSpec;
impl crate::RegisterSpec for RegSelSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`reg_sel::R`](R) reader structure"]
impl crate::Readable for RegSelSpec {}
#[doc = "`write(|w| ..)` method takes [`reg_sel::W`](W) writer structure"]
impl crate::Writable for RegSelSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets REG_SEL to value 0x02"]
impl crate::Resettable for RegSelSpec {
    const RESET_VALUE: u8 = 0x02;
}
