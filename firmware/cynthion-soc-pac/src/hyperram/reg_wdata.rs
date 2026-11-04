#[doc = "Register `REG_WDATA` reader"]
pub type R = crate::R<RegWdataSpec>;
#[doc = "Register `REG_WDATA` writer"]
pub type W = crate::W<RegWdataSpec>;
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
    pub fn v(&mut self) -> VW<'_, RegWdataSpec> {
        VW::new(self, 0)
    }
}
#[doc = "HYPERRAM.REG_WDATA, 16 bits at +0x2e\n\nYou can [`read`](crate::Reg::read) this register and get [`reg_wdata::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`reg_wdata::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RegWdataSpec;
impl crate::RegisterSpec for RegWdataSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`reg_wdata::R`](R) reader structure"]
impl crate::Readable for RegWdataSpec {}
#[doc = "`write(|w| ..)` method takes [`reg_wdata::W`](W) writer structure"]
impl crate::Writable for RegWdataSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets REG_WDATA to value 0x8f2f"]
impl crate::Resettable for RegWdataSpec {
    const RESET_VALUE: u16 = 0x8f2f;
}
