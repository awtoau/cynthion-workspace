#[doc = "Register `CTRL` reader"]
pub type R = crate::R<CtrlSpec>;
#[doc = "Register `CTRL` writer"]
pub type W = crate::W<CtrlSpec>;
#[doc = "Field `TARGET_C` reader - target_c \\[3\\]"]
pub type TargetCR = crate::BitReader;
#[doc = "Field `TARGET_C` writer - target_c \\[3\\]"]
pub type TargetCW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `CONTROL` reader - control \\[4\\]"]
pub type ControlR = crate::BitReader;
#[doc = "Field `CONTROL` writer - control \\[4\\]"]
pub type ControlW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `AUX` reader - aux \\[5\\]"]
pub type AuxR = crate::BitReader;
#[doc = "Field `AUX` writer - aux \\[5\\]"]
pub type AuxW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `DISCHARGE` reader - discharge \\[6\\]"]
pub type DischargeR = crate::BitReader;
#[doc = "Field `DISCHARGE` writer - discharge \\[6\\]"]
pub type DischargeW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `ENABLE` reader - enable \\[7\\]"]
pub type EnableR = crate::BitReader;
#[doc = "Field `ENABLE` writer - enable \\[7\\]"]
pub type EnableW<'a, REG> = crate::BitWriter<'a, REG>;
impl R {
    #[doc = "Bit 3 - target_c \\[3\\]"]
    #[inline(always)]
    pub fn target_c(&self) -> TargetCR {
        TargetCR::new(((self.bits >> 3) & 1) != 0)
    }
    #[doc = "Bit 4 - control \\[4\\]"]
    #[inline(always)]
    pub fn control(&self) -> ControlR {
        ControlR::new(((self.bits >> 4) & 1) != 0)
    }
    #[doc = "Bit 5 - aux \\[5\\]"]
    #[inline(always)]
    pub fn aux(&self) -> AuxR {
        AuxR::new(((self.bits >> 5) & 1) != 0)
    }
    #[doc = "Bit 6 - discharge \\[6\\]"]
    #[inline(always)]
    pub fn discharge(&self) -> DischargeR {
        DischargeR::new(((self.bits >> 6) & 1) != 0)
    }
    #[doc = "Bit 7 - enable \\[7\\]"]
    #[inline(always)]
    pub fn enable(&self) -> EnableR {
        EnableR::new(((self.bits >> 7) & 1) != 0)
    }
}
impl W {
    #[doc = "Bit 3 - target_c \\[3\\]"]
    #[inline(always)]
    pub fn target_c(&mut self) -> TargetCW<'_, CtrlSpec> {
        TargetCW::new(self, 3)
    }
    #[doc = "Bit 4 - control \\[4\\]"]
    #[inline(always)]
    pub fn control(&mut self) -> ControlW<'_, CtrlSpec> {
        ControlW::new(self, 4)
    }
    #[doc = "Bit 5 - aux \\[5\\]"]
    #[inline(always)]
    pub fn aux(&mut self) -> AuxW<'_, CtrlSpec> {
        AuxW::new(self, 5)
    }
    #[doc = "Bit 6 - discharge \\[6\\]"]
    #[inline(always)]
    pub fn discharge(&mut self) -> DischargeW<'_, CtrlSpec> {
        DischargeW::new(self, 6)
    }
    #[doc = "Bit 7 - enable \\[7\\]"]
    #[inline(always)]
    pub fn enable(&mut self) -> EnableW<'_, CtrlSpec> {
        EnableW::new(self, 7)
    }
}
#[doc = "BOARD_VBUS.CTRL, 8 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`ctrl::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ctrl::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CtrlSpec;
impl crate::RegisterSpec for CtrlSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`ctrl::R`](R) reader structure"]
impl crate::Readable for CtrlSpec {}
#[doc = "`write(|w| ..)` method takes [`ctrl::W`](W) writer structure"]
impl crate::Writable for CtrlSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets CTRL to value 0"]
impl crate::Resettable for CtrlSpec {}
