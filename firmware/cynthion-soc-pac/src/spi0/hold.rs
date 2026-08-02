#[doc = "Register `HOLD` reader"]
pub type R = crate::R<HoldSpec>;
#[doc = "Register `HOLD` writer"]
pub type W = crate::W<HoldSpec>;
#[doc = "Field `SELECT` reader - select \\[0\\]"]
pub type SelectR = crate::BitReader;
#[doc = "Field `SELECT` writer - select \\[0\\]"]
pub type SelectW<'a, REG> = crate::BitWriter<'a, REG>;
impl R {
    #[doc = "Bit 0 - select \\[0\\]"]
    #[inline(always)]
    pub fn select(&self) -> SelectR {
        SelectR::new((self.bits & 1) != 0)
    }
}
impl W {
    #[doc = "Bit 0 - select \\[0\\]"]
    #[inline(always)]
    pub fn select(&mut self) -> SelectW<'_, HoldSpec> {
        SelectW::new(self, 0)
    }
}
#[doc = "SPI0.HOLD, 1 bits at +0x20\n\nYou can [`read`](crate::Reg::read) this register and get [`hold::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`hold::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct HoldSpec;
impl crate::RegisterSpec for HoldSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`hold::R`](R) reader structure"]
impl crate::Readable for HoldSpec {}
#[doc = "`write(|w| ..)` method takes [`hold::W`](W) writer structure"]
impl crate::Writable for HoldSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets HOLD to value 0"]
impl crate::Resettable for HoldSpec {}
