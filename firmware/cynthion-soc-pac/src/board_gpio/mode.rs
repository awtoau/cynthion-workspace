#[doc = "Register `MODE` reader"]
pub type R = crate::R<ModeSpec>;
#[doc = "Register `MODE` writer"]
pub type W = crate::W<ModeSpec>;
#[doc = "Field `PIN_0` reader - pin_0 \\[1:0\\]"]
pub type Pin0R = crate::FieldReader;
#[doc = "Field `PIN_0` writer - pin_0 \\[1:0\\]"]
pub type Pin0W<'a, REG> = crate::FieldWriter<'a, REG, 2>;
#[doc = "Field `PIN_1` reader - pin_1 \\[3:2\\]"]
pub type Pin1R = crate::FieldReader;
#[doc = "Field `PIN_1` writer - pin_1 \\[3:2\\]"]
pub type Pin1W<'a, REG> = crate::FieldWriter<'a, REG, 2>;
#[doc = "Field `PIN_2` reader - pin_2 \\[5:4\\]"]
pub type Pin2R = crate::FieldReader;
#[doc = "Field `PIN_2` writer - pin_2 \\[5:4\\]"]
pub type Pin2W<'a, REG> = crate::FieldWriter<'a, REG, 2>;
#[doc = "Field `PIN_3` reader - pin_3 \\[7:6\\]"]
pub type Pin3R = crate::FieldReader;
#[doc = "Field `PIN_3` writer - pin_3 \\[7:6\\]"]
pub type Pin3W<'a, REG> = crate::FieldWriter<'a, REG, 2>;
#[doc = "Field `PIN_4` reader - pin_4 \\[9:8\\]"]
pub type Pin4R = crate::FieldReader;
#[doc = "Field `PIN_4` writer - pin_4 \\[9:8\\]"]
pub type Pin4W<'a, REG> = crate::FieldWriter<'a, REG, 2>;
#[doc = "Field `PIN_5` reader - pin_5 \\[11:10\\]"]
pub type Pin5R = crate::FieldReader;
#[doc = "Field `PIN_5` writer - pin_5 \\[11:10\\]"]
pub type Pin5W<'a, REG> = crate::FieldWriter<'a, REG, 2>;
#[doc = "Field `PIN_6` reader - pin_6 \\[13:12\\]"]
pub type Pin6R = crate::FieldReader;
#[doc = "Field `PIN_6` writer - pin_6 \\[13:12\\]"]
pub type Pin6W<'a, REG> = crate::FieldWriter<'a, REG, 2>;
#[doc = "Field `PIN_7` reader - pin_7 \\[15:14\\]"]
pub type Pin7R = crate::FieldReader;
#[doc = "Field `PIN_7` writer - pin_7 \\[15:14\\]"]
pub type Pin7W<'a, REG> = crate::FieldWriter<'a, REG, 2>;
impl R {
    #[doc = "Bits 0:1 - pin_0 \\[1:0\\]"]
    #[inline(always)]
    pub fn pin_0(&self) -> Pin0R {
        Pin0R::new((self.bits & 3) as u8)
    }
    #[doc = "Bits 2:3 - pin_1 \\[3:2\\]"]
    #[inline(always)]
    pub fn pin_1(&self) -> Pin1R {
        Pin1R::new(((self.bits >> 2) & 3) as u8)
    }
    #[doc = "Bits 4:5 - pin_2 \\[5:4\\]"]
    #[inline(always)]
    pub fn pin_2(&self) -> Pin2R {
        Pin2R::new(((self.bits >> 4) & 3) as u8)
    }
    #[doc = "Bits 6:7 - pin_3 \\[7:6\\]"]
    #[inline(always)]
    pub fn pin_3(&self) -> Pin3R {
        Pin3R::new(((self.bits >> 6) & 3) as u8)
    }
    #[doc = "Bits 8:9 - pin_4 \\[9:8\\]"]
    #[inline(always)]
    pub fn pin_4(&self) -> Pin4R {
        Pin4R::new(((self.bits >> 8) & 3) as u8)
    }
    #[doc = "Bits 10:11 - pin_5 \\[11:10\\]"]
    #[inline(always)]
    pub fn pin_5(&self) -> Pin5R {
        Pin5R::new(((self.bits >> 10) & 3) as u8)
    }
    #[doc = "Bits 12:13 - pin_6 \\[13:12\\]"]
    #[inline(always)]
    pub fn pin_6(&self) -> Pin6R {
        Pin6R::new(((self.bits >> 12) & 3) as u8)
    }
    #[doc = "Bits 14:15 - pin_7 \\[15:14\\]"]
    #[inline(always)]
    pub fn pin_7(&self) -> Pin7R {
        Pin7R::new(((self.bits >> 14) & 3) as u8)
    }
}
impl W {
    #[doc = "Bits 0:1 - pin_0 \\[1:0\\]"]
    #[inline(always)]
    pub fn pin_0(&mut self) -> Pin0W<'_, ModeSpec> {
        Pin0W::new(self, 0)
    }
    #[doc = "Bits 2:3 - pin_1 \\[3:2\\]"]
    #[inline(always)]
    pub fn pin_1(&mut self) -> Pin1W<'_, ModeSpec> {
        Pin1W::new(self, 2)
    }
    #[doc = "Bits 4:5 - pin_2 \\[5:4\\]"]
    #[inline(always)]
    pub fn pin_2(&mut self) -> Pin2W<'_, ModeSpec> {
        Pin2W::new(self, 4)
    }
    #[doc = "Bits 6:7 - pin_3 \\[7:6\\]"]
    #[inline(always)]
    pub fn pin_3(&mut self) -> Pin3W<'_, ModeSpec> {
        Pin3W::new(self, 6)
    }
    #[doc = "Bits 8:9 - pin_4 \\[9:8\\]"]
    #[inline(always)]
    pub fn pin_4(&mut self) -> Pin4W<'_, ModeSpec> {
        Pin4W::new(self, 8)
    }
    #[doc = "Bits 10:11 - pin_5 \\[11:10\\]"]
    #[inline(always)]
    pub fn pin_5(&mut self) -> Pin5W<'_, ModeSpec> {
        Pin5W::new(self, 10)
    }
    #[doc = "Bits 12:13 - pin_6 \\[13:12\\]"]
    #[inline(always)]
    pub fn pin_6(&mut self) -> Pin6W<'_, ModeSpec> {
        Pin6W::new(self, 12)
    }
    #[doc = "Bits 14:15 - pin_7 \\[15:14\\]"]
    #[inline(always)]
    pub fn pin_7(&mut self) -> Pin7W<'_, ModeSpec> {
        Pin7W::new(self, 14)
    }
}
#[doc = "BOARD_GPIO.MODE, 16 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`mode::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`mode::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct ModeSpec;
impl crate::RegisterSpec for ModeSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`mode::R`](R) reader structure"]
impl crate::Readable for ModeSpec {}
#[doc = "`write(|w| ..)` method takes [`mode::W`](W) writer structure"]
impl crate::Writable for ModeSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets MODE to value 0"]
impl crate::Resettable for ModeSpec {}
