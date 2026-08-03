#[doc = "Register `INPUT` reader"]
pub type R = crate::R<InputSpec>;
#[doc = "Register `INPUT` writer"]
pub type W = crate::W<InputSpec>;
#[doc = "Field `CONTROL_IN` reader - control_in \\[0\\]"]
pub type ControlInR = crate::BitReader;
#[doc = "Field `CONTROL_IN` writer - control_in \\[0\\]"]
pub type ControlInW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `AUX_IN` reader - aux_in \\[1\\]"]
pub type AuxInR = crate::BitReader;
#[doc = "Field `AUX_IN` writer - aux_in \\[1\\]"]
pub type AuxInW<'a, REG> = crate::BitWriter<'a, REG>;
impl R {
    #[doc = "Bit 0 - control_in \\[0\\]"]
    #[inline(always)]
    pub fn control_in(&self) -> ControlInR {
        ControlInR::new((self.bits & 1) != 0)
    }
    #[doc = "Bit 1 - aux_in \\[1\\]"]
    #[inline(always)]
    pub fn aux_in(&self) -> AuxInR {
        AuxInR::new(((self.bits >> 1) & 1) != 0)
    }
}
impl W {
    #[doc = "Bit 0 - control_in \\[0\\]"]
    #[inline(always)]
    pub fn control_in(&mut self) -> ControlInW<'_, InputSpec> {
        ControlInW::new(self, 0)
    }
    #[doc = "Bit 1 - aux_in \\[1\\]"]
    #[inline(always)]
    pub fn aux_in(&mut self) -> AuxInW<'_, InputSpec> {
        AuxInW::new(self, 1)
    }
}
#[doc = "BOARD_VBUS.INPUT, 8 bits at +0x01\n\nYou can [`read`](crate::Reg::read) this register and get [`input::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`input::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct InputSpec;
impl crate::RegisterSpec for InputSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`input::R`](R) reader structure"]
impl crate::Readable for InputSpec {}
#[doc = "`write(|w| ..)` method takes [`input::W`](W) writer structure"]
impl crate::Writable for InputSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets INPUT to value 0x01"]
impl crate::Resettable for InputSpec {
    const RESET_VALUE: u8 = 0x01;
}
