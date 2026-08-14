#[doc = "Register `CTRL` reader"]
pub type R = crate::R<CtrlSpec>;
#[doc = "Register `CTRL` writer"]
pub type W = crate::W<CtrlSpec>;
#[doc = "Field `SEL` reader - sel \\[0\\]"]
pub type SelR = crate::BitReader;
#[doc = "Field `SEL` writer - sel \\[0\\]"]
pub type SelW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `BIST` reader - bist \\[1\\]"]
pub type BistR = crate::BitReader;
#[doc = "Field `BIST` writer - bist \\[1\\]"]
pub type BistW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `REFUSED_CLEAR` writer - refused_clear \\[2\\]"]
pub type RefusedClearW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `REGS` reader - regs \\[3\\]"]
pub type RegsR = crate::BitReader;
#[doc = "Field `REGS` writer - regs \\[3\\]"]
pub type RegsW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `FIXED` reader - fixed \\[4\\]"]
pub type FixedR = crate::BitReader;
#[doc = "Field `FIXED` writer - fixed \\[4\\]"]
pub type FixedW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `LATENCY` reader - latency \\[10:5\\]"]
pub type LatencyR = crate::FieldReader;
#[doc = "Field `LATENCY` writer - latency \\[10:5\\]"]
pub type LatencyW<'a, REG> = crate::FieldWriter<'a, REG, 6>;
#[doc = "Field `LOW_LATENCY` reader - low_latency \\[16:11\\]"]
pub type LowLatencyR = crate::FieldReader;
#[doc = "Field `LOW_LATENCY` writer - low_latency \\[16:11\\]"]
pub type LowLatencyW<'a, REG> = crate::FieldWriter<'a, REG, 6>;
impl R {
    #[doc = "Bit 0 - sel \\[0\\]"]
    #[inline(always)]
    pub fn sel(&self) -> SelR {
        SelR::new((self.bits & 1) != 0)
    }
    #[doc = "Bit 1 - bist \\[1\\]"]
    #[inline(always)]
    pub fn bist(&self) -> BistR {
        BistR::new(((self.bits >> 1) & 1) != 0)
    }
    #[doc = "Bit 3 - regs \\[3\\]"]
    #[inline(always)]
    pub fn regs(&self) -> RegsR {
        RegsR::new(((self.bits >> 3) & 1) != 0)
    }
    #[doc = "Bit 4 - fixed \\[4\\]"]
    #[inline(always)]
    pub fn fixed(&self) -> FixedR {
        FixedR::new(((self.bits >> 4) & 1) != 0)
    }
    #[doc = "Bits 5:10 - latency \\[10:5\\]"]
    #[inline(always)]
    pub fn latency(&self) -> LatencyR {
        LatencyR::new(((self.bits >> 5) & 0x3f) as u8)
    }
    #[doc = "Bits 11:16 - low_latency \\[16:11\\]"]
    #[inline(always)]
    pub fn low_latency(&self) -> LowLatencyR {
        LowLatencyR::new(((self.bits >> 11) & 0x3f) as u8)
    }
}
impl W {
    #[doc = "Bit 0 - sel \\[0\\]"]
    #[inline(always)]
    pub fn sel(&mut self) -> SelW<'_, CtrlSpec> {
        SelW::new(self, 0)
    }
    #[doc = "Bit 1 - bist \\[1\\]"]
    #[inline(always)]
    pub fn bist(&mut self) -> BistW<'_, CtrlSpec> {
        BistW::new(self, 1)
    }
    #[doc = "Bit 2 - refused_clear \\[2\\]"]
    #[inline(always)]
    pub fn refused_clear(&mut self) -> RefusedClearW<'_, CtrlSpec> {
        RefusedClearW::new(self, 2)
    }
    #[doc = "Bit 3 - regs \\[3\\]"]
    #[inline(always)]
    pub fn regs(&mut self) -> RegsW<'_, CtrlSpec> {
        RegsW::new(self, 3)
    }
    #[doc = "Bit 4 - fixed \\[4\\]"]
    #[inline(always)]
    pub fn fixed(&mut self) -> FixedW<'_, CtrlSpec> {
        FixedW::new(self, 4)
    }
    #[doc = "Bits 5:10 - latency \\[10:5\\]"]
    #[inline(always)]
    pub fn latency(&mut self) -> LatencyW<'_, CtrlSpec> {
        LatencyW::new(self, 5)
    }
    #[doc = "Bits 11:16 - low_latency \\[16:11\\]"]
    #[inline(always)]
    pub fn low_latency(&mut self) -> LowLatencyW<'_, CtrlSpec> {
        LowLatencyW::new(self, 11)
    }
}
#[doc = "HYPERRAM_CK.CTRL, 32 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`ctrl::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ctrl::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CtrlSpec;
impl crate::RegisterSpec for CtrlSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`ctrl::R`](R) reader structure"]
impl crate::Readable for CtrlSpec {}
#[doc = "`write(|w| ..)` method takes [`ctrl::W`](W) writer structure"]
impl crate::Writable for CtrlSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets CTRL to value 0x10"]
impl crate::Resettable for CtrlSpec {
    const RESET_VALUE: u32 = 0x10;
}
