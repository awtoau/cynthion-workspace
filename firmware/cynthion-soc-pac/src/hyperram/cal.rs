#[doc = "Register `CAL` reader"]
pub type R = crate::R<CalSpec>;
#[doc = "Field `TAP` reader - tap \\[6:0\\]"]
pub type TapR = crate::FieldReader;
#[doc = "Field `_PAD` reader - _pad \\[7\\]"]
pub type _PadR = crate::BitReader;
#[doc = "Field `WIDTH` reader - width \\[15:8\\]"]
pub type WidthR = crate::FieldReader;
#[doc = "Field `VALID` reader - valid \\[16\\]"]
pub type ValidR = crate::BitReader;
impl R {
    #[doc = "Bits 0:6 - tap \\[6:0\\]"]
    #[inline(always)]
    pub fn tap(&self) -> TapR {
        TapR::new((self.bits & 0x7f) as u8)
    }
    #[doc = "Bit 7 - _pad \\[7\\]"]
    #[inline(always)]
    pub fn _pad(&self) -> _PadR {
        _PadR::new(((self.bits >> 7) & 1) != 0)
    }
    #[doc = "Bits 8:15 - width \\[15:8\\]"]
    #[inline(always)]
    pub fn width(&self) -> WidthR {
        WidthR::new(((self.bits >> 8) & 0xff) as u8)
    }
    #[doc = "Bit 16 - valid \\[16\\]"]
    #[inline(always)]
    pub fn valid(&self) -> ValidR {
        ValidR::new(((self.bits >> 16) & 1) != 0)
    }
}
#[doc = "HYPERRAM.CAL, 17 bits at +0x34\n\nYou can [`read`](crate::Reg::read) this register and get [`cal::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CalSpec;
impl crate::RegisterSpec for CalSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`cal::R`](R) reader structure"]
impl crate::Readable for CalSpec {}
#[doc = "`reset()` method sets CAL to value 0"]
impl crate::Resettable for CalSpec {}
