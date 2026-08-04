#[doc = "Register `STATUS` reader"]
pub type R = crate::R<StatusSpec>;
#[doc = "Field `DLL_LOCKED` reader - dll_locked \\[0\\]"]
pub type DllLockedR = crate::BitReader;
#[doc = "Field `DLL_READY` reader - dll_ready \\[1\\]"]
pub type DllReadyR = crate::BitReader;
#[doc = "Field `BURSTDET` reader - burstdet \\[2\\]"]
pub type BurstdetR = crate::BitReader;
impl R {
    #[doc = "Bit 0 - dll_locked \\[0\\]"]
    #[inline(always)]
    pub fn dll_locked(&self) -> DllLockedR {
        DllLockedR::new((self.bits & 1) != 0)
    }
    #[doc = "Bit 1 - dll_ready \\[1\\]"]
    #[inline(always)]
    pub fn dll_ready(&self) -> DllReadyR {
        DllReadyR::new(((self.bits >> 1) & 1) != 0)
    }
    #[doc = "Bit 2 - burstdet \\[2\\]"]
    #[inline(always)]
    pub fn burstdet(&self) -> BurstdetR {
        BurstdetR::new(((self.bits >> 2) & 1) != 0)
    }
}
#[doc = "HYPERRAM_PROBE.STATUS, 3 bits at +0x1c\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StatusSpec;
impl crate::RegisterSpec for StatusSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`status::R`](R) reader structure"]
impl crate::Readable for StatusSpec {}
#[doc = "`reset()` method sets STATUS to value 0"]
impl crate::Resettable for StatusSpec {}
