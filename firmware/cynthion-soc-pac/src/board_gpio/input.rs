#[doc = "Register `INPUT` reader"]
pub type R = crate::R<InputSpec>;
#[doc = "Field `PIN_0` reader - pin_0 \\[0\\]"]
pub type Pin0R = crate::BitReader;
#[doc = "Field `PIN_1` reader - pin_1 \\[1\\]"]
pub type Pin1R = crate::BitReader;
#[doc = "Field `PIN_2` reader - pin_2 \\[2\\]"]
pub type Pin2R = crate::BitReader;
#[doc = "Field `PIN_3` reader - pin_3 \\[3\\]"]
pub type Pin3R = crate::BitReader;
#[doc = "Field `PIN_4` reader - pin_4 \\[4\\]"]
pub type Pin4R = crate::BitReader;
#[doc = "Field `PIN_5` reader - pin_5 \\[5\\]"]
pub type Pin5R = crate::BitReader;
#[doc = "Field `PIN_6` reader - pin_6 \\[6\\]"]
pub type Pin6R = crate::BitReader;
#[doc = "Field `PIN_7` reader - pin_7 \\[7\\]"]
pub type Pin7R = crate::BitReader;
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
#[doc = "BOARD_GPIO.INPUT, 8 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`input::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct InputSpec;
impl crate::RegisterSpec for InputSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`input::R`](R) reader structure"]
impl crate::Readable for InputSpec {}
#[doc = "`reset()` method sets INPUT to value 0"]
impl crate::Resettable for InputSpec {}
