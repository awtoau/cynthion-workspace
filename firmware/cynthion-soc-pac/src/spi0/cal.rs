#[doc = "Register `CAL` reader"]
pub type R = crate::R<CalSpec>;
#[doc = "Field `STEP` reader - step \\[7:0\\]"]
pub type StepR = crate::FieldReader;
#[doc = "Field `LAT` reader - lat \\[11:8\\]"]
pub type LatR = crate::FieldReader;
#[doc = "Field `XFER` reader - xfer \\[12\\]"]
pub type XferR = crate::BitReader;
#[doc = "Field `_PAD` reader - _pad \\[15:13\\]"]
pub type _PadR = crate::FieldReader;
#[doc = "Field `WIDTH_X1` reader - width_x1 \\[23:16\\]"]
pub type WidthX1R = crate::FieldReader;
#[doc = "Field `WIDTH_X4` reader - width_x4 \\[31:24\\]"]
pub type WidthX4R = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - step \\[7:0\\]"]
    #[inline(always)]
    pub fn step(&self) -> StepR {
        StepR::new((self.bits & 0xff) as u8)
    }
    #[doc = "Bits 8:11 - lat \\[11:8\\]"]
    #[inline(always)]
    pub fn lat(&self) -> LatR {
        LatR::new(((self.bits >> 8) & 0x0f) as u8)
    }
    #[doc = "Bit 12 - xfer \\[12\\]"]
    #[inline(always)]
    pub fn xfer(&self) -> XferR {
        XferR::new(((self.bits >> 12) & 1) != 0)
    }
    #[doc = "Bits 13:15 - _pad \\[15:13\\]"]
    #[inline(always)]
    pub fn _pad(&self) -> _PadR {
        _PadR::new(((self.bits >> 13) & 7) as u8)
    }
    #[doc = "Bits 16:23 - width_x1 \\[23:16\\]"]
    #[inline(always)]
    pub fn width_x1(&self) -> WidthX1R {
        WidthX1R::new(((self.bits >> 16) & 0xff) as u8)
    }
    #[doc = "Bits 24:31 - width_x4 \\[31:24\\]"]
    #[inline(always)]
    pub fn width_x4(&self) -> WidthX4R {
        WidthX4R::new(((self.bits >> 24) & 0xff) as u8)
    }
}
#[doc = "SPI0.CAL, 32 bits at +0x28\n\nYou can [`read`](crate::Reg::read) this register and get [`cal::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CalSpec;
impl crate::RegisterSpec for CalSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`cal::R`](R) reader structure"]
impl crate::Readable for CalSpec {}
#[doc = "`reset()` method sets CAL to value 0"]
impl crate::Resettable for CalSpec {}
