#[doc = "Register `FLAGS` reader"]
pub type R = crate::R<FlagsSpec>;
#[doc = "Register `FLAGS` writer"]
pub type W = crate::W<FlagsSpec>;
#[doc = "Field `WREN` reader - wren \\[0\\]"]
pub type WrenR = crate::BitReader;
#[doc = "Field `WREN` writer - wren \\[0\\]"]
pub type WrenW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `WAIT` reader - wait \\[1\\]"]
pub type WaitR = crate::BitReader;
#[doc = "Field `WAIT` writer - wait \\[1\\]"]
pub type WaitW<'a, REG> = crate::BitWriter<'a, REG>;
impl R {
    #[doc = "Bit 0 - wren \\[0\\]"]
    #[inline(always)]
    pub fn wren(&self) -> WrenR {
        WrenR::new((self.bits & 1) != 0)
    }
    #[doc = "Bit 1 - wait \\[1\\]"]
    #[inline(always)]
    pub fn wait(&self) -> WaitR {
        WaitR::new(((self.bits >> 1) & 1) != 0)
    }
}
impl W {
    #[doc = "Bit 0 - wren \\[0\\]"]
    #[inline(always)]
    pub fn wren(&mut self) -> WrenW<'_, FlagsSpec> {
        WrenW::new(self, 0)
    }
    #[doc = "Bit 1 - wait \\[1\\]"]
    #[inline(always)]
    pub fn wait(&mut self) -> WaitW<'_, FlagsSpec> {
        WaitW::new(self, 1)
    }
}
#[doc = "SPI0.FLAGS, 2 bits at +0x1e\n\nYou can [`read`](crate::Reg::read) this register and get [`flags::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`flags::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct FlagsSpec;
impl crate::RegisterSpec for FlagsSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`flags::R`](R) reader structure"]
impl crate::Readable for FlagsSpec {}
#[doc = "`write(|w| ..)` method takes [`flags::W`](W) writer structure"]
impl crate::Writable for FlagsSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets FLAGS to value 0"]
impl crate::Resettable for FlagsSpec {}
