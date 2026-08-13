#[doc = "Register `PENDING` reader"]
pub type R = crate::R<PendingSpec>;
#[doc = "Register `PENDING` writer"]
pub type W = crate::W<PendingSpec>;
#[doc = "Field `MASK` reader - mask \\[17:0\\]"]
pub type MaskR = crate::FieldReader<u32>;
#[doc = "Field `MASK` writer - mask \\[17:0\\]"]
pub type MaskW<'a, REG> = crate::FieldWriter<'a, REG, 18, u32>;
impl R {
    #[doc = "Bits 0:17 - mask \\[17:0\\]"]
    #[inline(always)]
    pub fn mask(&self) -> MaskR {
        MaskR::new(self.bits & 0x0003_ffff)
    }
}
impl W {
    #[doc = "Bits 0:17 - mask \\[17:0\\]"]
    #[inline(always)]
    pub fn mask(&mut self) -> MaskW<'_, PendingSpec> {
        MaskW::new(self, 0)
    }
}
#[doc = "INTC.PENDING, 18 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`pending::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`pending::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct PendingSpec;
impl crate::RegisterSpec for PendingSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`pending::R`](R) reader structure"]
impl crate::Readable for PendingSpec {}
#[doc = "`write(|w| ..)` method takes [`pending::W`](W) writer structure"]
impl crate::Writable for PendingSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets PENDING to value 0"]
impl crate::Resettable for PendingSpec {}
