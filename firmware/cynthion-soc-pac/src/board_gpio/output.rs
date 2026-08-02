#[doc = "Register `OUTPUT` reader"]
pub type R = crate::R<OutputSpec>;
#[doc = "Register `OUTPUT` writer"]
pub type W = crate::W<OutputSpec>;
#[doc = "Field `PIN_0` reader - pin_0 \\[0\\]"]
pub type Pin0R = crate::BitReader;
#[doc = "Field `PIN_0` writer - pin_0 \\[0\\]"]
pub type Pin0W<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_1` reader - pin_1 \\[1\\]"]
pub type Pin1R = crate::BitReader;
#[doc = "Field `PIN_1` writer - pin_1 \\[1\\]"]
pub type Pin1W<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_2` reader - pin_2 \\[2\\]"]
pub type Pin2R = crate::BitReader;
#[doc = "Field `PIN_2` writer - pin_2 \\[2\\]"]
pub type Pin2W<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_3` reader - pin_3 \\[3\\]"]
pub type Pin3R = crate::BitReader;
#[doc = "Field `PIN_3` writer - pin_3 \\[3\\]"]
pub type Pin3W<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_4` reader - pin_4 \\[4\\]"]
pub type Pin4R = crate::BitReader;
#[doc = "Field `PIN_4` writer - pin_4 \\[4\\]"]
pub type Pin4W<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_5` reader - pin_5 \\[5\\]"]
pub type Pin5R = crate::BitReader;
#[doc = "Field `PIN_5` writer - pin_5 \\[5\\]"]
pub type Pin5W<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_6` reader - pin_6 \\[6\\]"]
pub type Pin6R = crate::BitReader;
#[doc = "Field `PIN_6` writer - pin_6 \\[6\\]"]
pub type Pin6W<'a, REG> = crate::BitWriter<'a, REG>;
#[doc = "Field `PIN_7` reader - pin_7 \\[7\\]"]
pub type Pin7R = crate::BitReader;
#[doc = "Field `PIN_7` writer - pin_7 \\[7\\]"]
pub type Pin7W<'a, REG> = crate::BitWriter<'a, REG>;
impl R {
    #[doc = "Bit 0 - pin_0 \\[0\\]"]
    #[inline(always)]
    pub fn pin_0(&self) -> Pin0R {
        Pin0R::new((self.bits & 1) != 0)
    }
    #[doc = "Bit 1 - pin_1 \\[1\\]"]
    #[inline(always)]
    pub fn pin_1(&self) -> Pin1R {
        Pin1R::new(((self.bits >> 1) & 1) != 0)
    }
    #[doc = "Bit 2 - pin_2 \\[2\\]"]
    #[inline(always)]
    pub fn pin_2(&self) -> Pin2R {
        Pin2R::new(((self.bits >> 2) & 1) != 0)
    }
    #[doc = "Bit 3 - pin_3 \\[3\\]"]
    #[inline(always)]
    pub fn pin_3(&self) -> Pin3R {
        Pin3R::new(((self.bits >> 3) & 1) != 0)
    }
    #[doc = "Bit 4 - pin_4 \\[4\\]"]
    #[inline(always)]
    pub fn pin_4(&self) -> Pin4R {
        Pin4R::new(((self.bits >> 4) & 1) != 0)
    }
    #[doc = "Bit 5 - pin_5 \\[5\\]"]
    #[inline(always)]
    pub fn pin_5(&self) -> Pin5R {
        Pin5R::new(((self.bits >> 5) & 1) != 0)
    }
    #[doc = "Bit 6 - pin_6 \\[6\\]"]
    #[inline(always)]
    pub fn pin_6(&self) -> Pin6R {
        Pin6R::new(((self.bits >> 6) & 1) != 0)
    }
    #[doc = "Bit 7 - pin_7 \\[7\\]"]
    #[inline(always)]
    pub fn pin_7(&self) -> Pin7R {
        Pin7R::new(((self.bits >> 7) & 1) != 0)
    }
}
impl W {
    #[doc = "Bit 0 - pin_0 \\[0\\]"]
    #[inline(always)]
    pub fn pin_0(&mut self) -> Pin0W<'_, OutputSpec> {
        Pin0W::new(self, 0)
    }
    #[doc = "Bit 1 - pin_1 \\[1\\]"]
    #[inline(always)]
    pub fn pin_1(&mut self) -> Pin1W<'_, OutputSpec> {
        Pin1W::new(self, 1)
    }
    #[doc = "Bit 2 - pin_2 \\[2\\]"]
    #[inline(always)]
    pub fn pin_2(&mut self) -> Pin2W<'_, OutputSpec> {
        Pin2W::new(self, 2)
    }
    #[doc = "Bit 3 - pin_3 \\[3\\]"]
    #[inline(always)]
    pub fn pin_3(&mut self) -> Pin3W<'_, OutputSpec> {
        Pin3W::new(self, 3)
    }
    #[doc = "Bit 4 - pin_4 \\[4\\]"]
    #[inline(always)]
    pub fn pin_4(&mut self) -> Pin4W<'_, OutputSpec> {
        Pin4W::new(self, 4)
    }
    #[doc = "Bit 5 - pin_5 \\[5\\]"]
    #[inline(always)]
    pub fn pin_5(&mut self) -> Pin5W<'_, OutputSpec> {
        Pin5W::new(self, 5)
    }
    #[doc = "Bit 6 - pin_6 \\[6\\]"]
    #[inline(always)]
    pub fn pin_6(&mut self) -> Pin6W<'_, OutputSpec> {
        Pin6W::new(self, 6)
    }
    #[doc = "Bit 7 - pin_7 \\[7\\]"]
    #[inline(always)]
    pub fn pin_7(&mut self) -> Pin7W<'_, OutputSpec> {
        Pin7W::new(self, 7)
    }
}
#[doc = "BOARD_GPIO.OUTPUT, 8 bits at +0x03\n\nYou can [`read`](crate::Reg::read) this register and get [`output::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`output::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct OutputSpec;
impl crate::RegisterSpec for OutputSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`output::R`](R) reader structure"]
impl crate::Readable for OutputSpec {}
#[doc = "`write(|w| ..)` method takes [`output::W`](W) writer structure"]
impl crate::Writable for OutputSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets OUTPUT to value 0"]
impl crate::Resettable for OutputSpec {}
