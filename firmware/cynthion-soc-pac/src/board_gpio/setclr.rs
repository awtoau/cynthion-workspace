#[doc = "Register `SETCLR` writer"]
pub type W = crate::W<SetclrSpec>;
#[doc = "Field `PIN_0_SET` writer - pin_0_set \\[0\\]"]
pub type Pin0SetW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_0_CLR` writer - pin_0_clr \\[1\\]"]
pub type Pin0ClrW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_1_SET` writer - pin_1_set \\[2\\]"]
pub type Pin1SetW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_1_CLR` writer - pin_1_clr \\[3\\]"]
pub type Pin1ClrW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_2_SET` writer - pin_2_set \\[4\\]"]
pub type Pin2SetW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_2_CLR` writer - pin_2_clr \\[5\\]"]
pub type Pin2ClrW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_3_SET` writer - pin_3_set \\[6\\]"]
pub type Pin3SetW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_3_CLR` writer - pin_3_clr \\[7\\]"]
pub type Pin3ClrW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_4_SET` writer - pin_4_set \\[8\\]"]
pub type Pin4SetW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_4_CLR` writer - pin_4_clr \\[9\\]"]
pub type Pin4ClrW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_5_SET` writer - pin_5_set \\[10\\]"]
pub type Pin5SetW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_5_CLR` writer - pin_5_clr \\[11\\]"]
pub type Pin5ClrW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_6_SET` writer - pin_6_set \\[12\\]"]
pub type Pin6SetW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_6_CLR` writer - pin_6_clr \\[13\\]"]
pub type Pin6ClrW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_7_SET` writer - pin_7_set \\[14\\]"]
pub type Pin7SetW<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_7_CLR` writer - pin_7_clr \\[15\\]"]
pub type Pin7ClrW<'a, REG> = crate::BitWriter<'a, REG>;
impl W {
    #[doc = "Bit 0 - pin_0_set \\[0\\]"]
    #[inline(always)]
    pub fn pin_0_set(&mut self) -> Pin0SetW<'_, SetclrSpec> {
        Pin0SetW::new(self, 0)
    }
    #[doc = "Bit 1 - pin_0_clr \\[1\\]"]
    #[inline(always)]
    pub fn pin_0_clr(&mut self) -> Pin0ClrW<'_, SetclrSpec> {
        Pin0ClrW::new(self, 1)
    }
    #[doc = "Bit 2 - pin_1_set \\[2\\]"]
    #[inline(always)]
    pub fn pin_1_set(&mut self) -> Pin1SetW<'_, SetclrSpec> {
        Pin1SetW::new(self, 2)
    }
    #[doc = "Bit 3 - pin_1_clr \\[3\\]"]
    #[inline(always)]
    pub fn pin_1_clr(&mut self) -> Pin1ClrW<'_, SetclrSpec> {
        Pin1ClrW::new(self, 3)
    }
    #[doc = "Bit 4 - pin_2_set \\[4\\]"]
    #[inline(always)]
    pub fn pin_2_set(&mut self) -> Pin2SetW<'_, SetclrSpec> {
        Pin2SetW::new(self, 4)
    }
    #[doc = "Bit 5 - pin_2_clr \\[5\\]"]
    #[inline(always)]
    pub fn pin_2_clr(&mut self) -> Pin2ClrW<'_, SetclrSpec> {
        Pin2ClrW::new(self, 5)
    }
    #[doc = "Bit 6 - pin_3_set \\[6\\]"]
    #[inline(always)]
    pub fn pin_3_set(&mut self) -> Pin3SetW<'_, SetclrSpec> {
        Pin3SetW::new(self, 6)
    }
    #[doc = "Bit 7 - pin_3_clr \\[7\\]"]
    #[inline(always)]
    pub fn pin_3_clr(&mut self) -> Pin3ClrW<'_, SetclrSpec> {
        Pin3ClrW::new(self, 7)
    }
    #[doc = "Bit 8 - pin_4_set \\[8\\]"]
    #[inline(always)]
    pub fn pin_4_set(&mut self) -> Pin4SetW<'_, SetclrSpec> {
        Pin4SetW::new(self, 8)
    }
    #[doc = "Bit 9 - pin_4_clr \\[9\\]"]
    #[inline(always)]
    pub fn pin_4_clr(&mut self) -> Pin4ClrW<'_, SetclrSpec> {
        Pin4ClrW::new(self, 9)
    }
    #[doc = "Bit 10 - pin_5_set \\[10\\]"]
    #[inline(always)]
    pub fn pin_5_set(&mut self) -> Pin5SetW<'_, SetclrSpec> {
        Pin5SetW::new(self, 10)
    }
    #[doc = "Bit 11 - pin_5_clr \\[11\\]"]
    #[inline(always)]
    pub fn pin_5_clr(&mut self) -> Pin5ClrW<'_, SetclrSpec> {
        Pin5ClrW::new(self, 11)
    }
    #[doc = "Bit 12 - pin_6_set \\[12\\]"]
    #[inline(always)]
    pub fn pin_6_set(&mut self) -> Pin6SetW<'_, SetclrSpec> {
        Pin6SetW::new(self, 12)
    }
    #[doc = "Bit 13 - pin_6_clr \\[13\\]"]
    #[inline(always)]
    pub fn pin_6_clr(&mut self) -> Pin6ClrW<'_, SetclrSpec> {
        Pin6ClrW::new(self, 13)
    }
    #[doc = "Bit 14 - pin_7_set \\[14\\]"]
    #[inline(always)]
    pub fn pin_7_set(&mut self) -> Pin7SetW<'_, SetclrSpec> {
        Pin7SetW::new(self, 14)
    }
    #[doc = "Bit 15 - pin_7_clr \\[15\\]"]
    #[inline(always)]
    pub fn pin_7_clr(&mut self) -> Pin7ClrW<'_, SetclrSpec> {
        Pin7ClrW::new(self, 15)
    }
}
#[doc = "BOARD_GPIO.SETCLR, 16 bits at +0x04\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`setclr::W`](W). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct SetclrSpec;
impl crate::RegisterSpec for SetclrSpec {
    type Ux = u16;
}
#[doc = "`write(|w| ..)` method takes [`setclr::W`](W) writer structure"]
impl crate::Writable for SetclrSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets SETCLR to value 0"]
impl crate::Resettable for SetclrSpec {}
