#[doc = "Register `TR_STATE` reader"]
pub type R = crate::R<TrStateSpec>;
#[doc = "Field `STATE` reader - state \\[1:0\\]"]
pub type StateR = crate::FieldReader;
#[doc = "Field `BUSY` reader - busy \\[2\\]"]
pub type BusyR = crate::BitReader;
#[doc = "Field `PRESENT` reader - present \\[3\\]"]
pub type PresentR = crate::BitReader;
impl R {
    #[doc = "Bits 0:1 - state \\[1:0\\]"]
    #[inline(always)]
    pub fn state(&self) -> StateR {
        StateR::new(self.bits & 3)
    }
    #[doc = "Bit 2 - busy \\[2\\]"]
    #[inline(always)]
    pub fn busy(&self) -> BusyR {
        BusyR::new(((self.bits >> 2) & 1) != 0)
    }
    #[doc = "Bit 3 - present \\[3\\]"]
    #[inline(always)]
    pub fn present(&self) -> PresentR {
        PresentR::new(((self.bits >> 3) & 1) != 0)
    }
}
#[doc = "USB_HOST.TR_STATE, 4 bits at +0x19\n\nYou can [`read`](crate::Reg::read) this register and get [`tr_state::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct TrStateSpec;
impl crate::RegisterSpec for TrStateSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`tr_state::R`](R) reader structure"]
impl crate::Readable for TrStateSpec {}
#[doc = "`reset()` method sets TR_STATE to value 0"]
impl crate::Resettable for TrStateSpec {}
