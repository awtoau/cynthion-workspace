#[doc = "Register `BUF_ADDR` reader"]
pub type R = crate::R<BufAddrSpec>;
#[doc = "Register `BUF_ADDR` writer"]
pub type W = crate::W<BufAddrSpec>;
#[doc = "Field `V` reader - v \\[11:0\\]"]
pub type VR = crate::FieldReader<u16>;
#[doc = "Field `V` writer - v \\[11:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 12, u16>;
impl R {
    #[doc = "Bits 0:11 - v \\[11:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 0x0fff)
    }
}
impl W {
    #[doc = "Bits 0:11 - v \\[11:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, BufAddrSpec> {
        VW::new(self, 0)
    }
}
#[doc = "FLASH.BUF_ADDR, 12 bits at +0x18\n\nYou can [`read`](crate::Reg::read) this register and get [`buf_addr::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`buf_addr::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct BufAddrSpec;
impl crate::RegisterSpec for BufAddrSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`buf_addr::R`](R) reader structure"]
impl crate::Readable for BufAddrSpec {}
#[doc = "`write(|w| ..)` method takes [`buf_addr::W`](W) writer structure"]
impl crate::Writable for BufAddrSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets BUF_ADDR to value 0"]
impl crate::Resettable for BufAddrSpec {}
