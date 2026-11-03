#[doc = "Register `ADDR` reader"]
pub type R = crate::R<AddrSpec>;
#[doc = "Register `ADDR` writer"]
pub type W = crate::W<AddrSpec>;
#[doc = "Field `V` reader - v \\[21:0\\]"]
pub type VR = crate::FieldReader<u32>;
#[doc = "Field `V` writer - v \\[21:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 22, u32>;
impl R {
    #[doc = "Bits 0:21 - v \\[21:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 0x003f_ffff)
    }
}
impl W {
    #[doc = "Bits 0:21 - v \\[21:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, AddrSpec> {
        VW::new(self, 0)
    }
}
#[doc = "FLASH.ADDR, 22 bits at +0x14\n\nYou can [`read`](crate::Reg::read) this register and get [`addr::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`addr::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct AddrSpec;
impl crate::RegisterSpec for AddrSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`addr::R`](R) reader structure"]
impl crate::Readable for AddrSpec {}
#[doc = "`write(|w| ..)` method takes [`addr::W`](W) writer structure"]
impl crate::Writable for AddrSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets ADDR to value 0"]
impl crate::Resettable for AddrSpec {}
