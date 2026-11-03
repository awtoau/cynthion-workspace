#[doc = "Register `CAL_ADDR` reader"]
pub type R = crate::R<CalAddrSpec>;
#[doc = "Register `CAL_ADDR` writer"]
pub type W = crate::W<CalAddrSpec>;
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
    pub fn v(&mut self) -> VW<'_, CalAddrSpec> {
        VW::new(self, 0)
    }
}
#[doc = "FLASH.CAL_ADDR, 22 bits at +0x28\n\nYou can [`read`](crate::Reg::read) this register and get [`cal_addr::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`cal_addr::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CalAddrSpec;
impl crate::RegisterSpec for CalAddrSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`cal_addr::R`](R) reader structure"]
impl crate::Readable for CalAddrSpec {}
#[doc = "`write(|w| ..)` method takes [`cal_addr::W`](W) writer structure"]
impl crate::Writable for CalAddrSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets CAL_ADDR to value 0"]
impl crate::Resettable for CalAddrSpec {}
