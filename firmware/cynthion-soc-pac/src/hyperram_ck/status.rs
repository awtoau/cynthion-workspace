#[doc = "Register `STATUS` reader"]
pub type R = crate::R<StatusSpec>;
#[doc = "Field `LOCKED` reader - locked \\[0\\]"]
pub type LockedR = crate::BitReader;
#[doc = "Field `MODE` reader - mode \\[1\\]"]
pub type ModeR = crate::BitReader;
#[doc = "Field `REFUSED` reader - refused \\[2\\]"]
pub type RefusedR = crate::BitReader;
#[doc = "Field `RUNGS` reader - rungs \\[7:4\\]"]
pub type RungsR = crate::FieldReader;
#[doc = "Field `PAD` reader - pad \\[31:8\\]"]
pub type PadR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bit 0 - locked \\[0\\]"]
    #[inline(always)]
    pub fn locked(&self) -> LockedR {
        LockedR::new((self.bits & 1) != 0)
    }
    #[doc = "Bit 1 - mode \\[1\\]"]
    #[inline(always)]
    pub fn mode(&self) -> ModeR {
        ModeR::new(((self.bits >> 1) & 1) != 0)
    }
    #[doc = "Bit 2 - refused \\[2\\]"]
    #[inline(always)]
    pub fn refused(&self) -> RefusedR {
        RefusedR::new(((self.bits >> 2) & 1) != 0)
    }
    #[doc = "Bits 4:7 - rungs \\[7:4\\]"]
    #[inline(always)]
    pub fn rungs(&self) -> RungsR {
        RungsR::new(((self.bits >> 4) & 0x0f) as u8)
    }
    #[doc = "Bits 8:31 - pad \\[31:8\\]"]
    #[inline(always)]
    pub fn pad(&self) -> PadR {
        PadR::new((self.bits >> 8) & 0x00ff_ffff)
    }
}
#[doc = "HYPERRAM_CK.STATUS, 32 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StatusSpec;
impl crate::RegisterSpec for StatusSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`status::R`](R) reader structure"]
impl crate::Readable for StatusSpec {}
#[doc = "`reset()` method sets STATUS to value 0"]
impl crate::Resettable for StatusSpec {}
