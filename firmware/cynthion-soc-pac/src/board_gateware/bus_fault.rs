#[doc = "Register `BUS_FAULT` reader"]
pub type R = crate::R<BusFaultSpec>;
#[doc = "Field `UNCLAIMED` reader - unclaimed \\[7:0\\]"]
pub type UnclaimedR = crate::FieldReader;
#[doc = "Field `TIMEOUTS` reader - timeouts \\[15:8\\]"]
pub type TimeoutsR = crate::FieldReader;
#[doc = "Field `WORST` reader - worst \\[31:16\\]"]
pub type WorstR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:7 - unclaimed \\[7:0\\]"]
    #[inline(always)]
    pub fn unclaimed(&self) -> UnclaimedR {
        UnclaimedR::new((self.bits & 0xff) as u8)
    }
    #[doc = "Bits 8:15 - timeouts \\[15:8\\]"]
    #[inline(always)]
    pub fn timeouts(&self) -> TimeoutsR {
        TimeoutsR::new(((self.bits >> 8) & 0xff) as u8)
    }
    #[doc = "Bits 16:31 - worst \\[31:16\\]"]
    #[inline(always)]
    pub fn worst(&self) -> WorstR {
        WorstR::new(((self.bits >> 16) & 0xffff) as u16)
    }
}
#[doc = "BOARD_GATEWARE.BUS_FAULT, 32 bits at +0x1c\n\nYou can [`read`](crate::Reg::read) this register and get [`bus_fault::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct BusFaultSpec;
impl crate::RegisterSpec for BusFaultSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`bus_fault::R`](R) reader structure"]
impl crate::Readable for BusFaultSpec {}
#[doc = "`reset()` method sets BUS_FAULT to value 0"]
impl crate::Resettable for BusFaultSpec {}
