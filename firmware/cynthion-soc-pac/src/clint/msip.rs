#[doc = "Register `MSIP` reader"]
pub type R = crate::R<MsipSpec>;
#[doc = "Register `MSIP` writer"]
pub type W = crate::W<MsipSpec>;
#[doc = "Field `PENDING` reader - pending \\[0\\]"]
pub type PendingR = crate::BitReader;
#[doc = "Field `PENDING` writer - pending \\[0\\]"]
pub type PendingW<'a, REG> = crate::BitWriter<'a, REG>;
impl R {
    #[doc = "Bit 0 - pending \\[0\\]"]
    #[inline(always)]
    pub fn pending(&self) -> PendingR {
        PendingR::new((self.bits & 1) != 0)
    }
}
impl W {
    #[doc = "Bit 0 - pending \\[0\\]"]
    #[inline(always)]
    pub fn pending(&mut self) -> PendingW<'_, MsipSpec> {
        PendingW::new(self, 0)
    }
}
#[doc = "CLINT.MSIP, 1 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`msip::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`msip::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct MsipSpec;
impl crate::RegisterSpec for MsipSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`msip::R`](R) reader structure"]
impl crate::Readable for MsipSpec {}
#[doc = "`write(|w| ..)` method takes [`msip::W`](W) writer structure"]
impl crate::Writable for MsipSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets MSIP to value 0"]
impl crate::Resettable for MsipSpec {}
