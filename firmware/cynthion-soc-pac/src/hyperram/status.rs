#[doc = "Register `STATUS` reader"]
pub type R = crate::R<StatusSpec>;
#[doc = "Field `BUSY` reader - busy \\[0\\]"]
pub type BusyR = crate::BitReader;
#[doc = "Field `READY` reader - ready \\[1\\]"]
pub type ReadyR = crate::BitReader;
#[doc = "Field `LOCKED` reader - locked \\[2\\]"]
pub type LockedR = crate::BitReader;
#[doc = "Field `CAL_VALID` reader - cal_valid \\[3\\]"]
pub type CalValidR = crate::BitReader;
#[doc = "Field `STREAM_ON` reader - stream_on \\[4\\]"]
pub type StreamOnR = crate::BitReader;
#[doc = "Field `HR_BUSY` reader - hr_busy \\[5\\]"]
pub type HrBusyR = crate::BitReader;
#[doc = "Field `_PAD` reader - _pad \\[7:6\\]"]
pub type _PadR = crate::FieldReader;
#[doc = "Field `CODE` reader - code \\[11:8\\]"]
pub type CodeR = crate::FieldReader;
#[doc = "Field `OP` reader - op \\[15:12\\]"]
pub type OpR = crate::FieldReader;
impl R {
    #[doc = "Bit 0 - busy \\[0\\]"]
    #[inline(always)]
    pub fn busy(&self) -> BusyR {
        BusyR::new((self.bits & 1) != 0)
    }
    #[doc = "Bit 1 - ready \\[1\\]"]
    #[inline(always)]
    pub fn ready(&self) -> ReadyR {
        ReadyR::new(((self.bits >> 1) & 1) != 0)
    }
    #[doc = "Bit 2 - locked \\[2\\]"]
    #[inline(always)]
    pub fn locked(&self) -> LockedR {
        LockedR::new(((self.bits >> 2) & 1) != 0)
    }
    #[doc = "Bit 3 - cal_valid \\[3\\]"]
    #[inline(always)]
    pub fn cal_valid(&self) -> CalValidR {
        CalValidR::new(((self.bits >> 3) & 1) != 0)
    }
    #[doc = "Bit 4 - stream_on \\[4\\]"]
    #[inline(always)]
    pub fn stream_on(&self) -> StreamOnR {
        StreamOnR::new(((self.bits >> 4) & 1) != 0)
    }
    #[doc = "Bit 5 - hr_busy \\[5\\]"]
    #[inline(always)]
    pub fn hr_busy(&self) -> HrBusyR {
        HrBusyR::new(((self.bits >> 5) & 1) != 0)
    }
    #[doc = "Bits 6:7 - _pad \\[7:6\\]"]
    #[inline(always)]
    pub fn _pad(&self) -> _PadR {
        _PadR::new(((self.bits >> 6) & 3) as u8)
    }
    #[doc = "Bits 8:11 - code \\[11:8\\]"]
    #[inline(always)]
    pub fn code(&self) -> CodeR {
        CodeR::new(((self.bits >> 8) & 0x0f) as u8)
    }
    #[doc = "Bits 12:15 - op \\[15:12\\]"]
    #[inline(always)]
    pub fn op(&self) -> OpR {
        OpR::new(((self.bits >> 12) & 0x0f) as u8)
    }
}
#[doc = "HYPERRAM.STATUS, 16 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StatusSpec;
impl crate::RegisterSpec for StatusSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`status::R`](R) reader structure"]
impl crate::Readable for StatusSpec {}
#[doc = "`reset()` method sets STATUS to value 0"]
impl crate::Resettable for StatusSpec {}
