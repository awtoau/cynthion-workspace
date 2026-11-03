#[doc = "Register `HR_ADDR` reader"]
pub type R = crate::R<HrAddrSpec>;
#[doc = "Register `HR_ADDR` writer"]
pub type W = crate::W<HrAddrSpec>;
#[doc = "Field `V` reader - v \\[22:0\\]"]
pub type VR = crate::FieldReader<u32>;
#[doc = "Field `V` writer - v \\[22:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 23, u32>;
impl R {
    #[doc = "Bits 0:22 - v \\[22:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 0x007f_ffff)
    }
}
impl W {
    #[doc = "Bits 0:22 - v \\[22:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, HrAddrSpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.HR_ADDR, 23 bits at +0x14\n\nYou can [`read`](crate::Reg::read) this register and get [`hr_addr::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`hr_addr::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct HrAddrSpec;
impl crate::RegisterSpec for HrAddrSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`hr_addr::R`](R) reader structure"]
impl crate::Readable for HrAddrSpec {}
#[doc = "`write(|w| ..)` method takes [`hr_addr::W`](W) writer structure"]
impl crate::Writable for HrAddrSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets HR_ADDR to value 0"]
impl crate::Resettable for HrAddrSpec {}
