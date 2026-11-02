#[doc = "Register `LINK` reader"]
pub type R = crate::R<LinkSpec>;
#[doc = "Field `LINE_STATE` reader - line_state \\[1:0\\]"]
pub type LineStateR = crate::FieldReader;
#[doc = "Field `SPEED` reader - speed \\[3:2\\]"]
pub type SpeedR = crate::FieldReader;
#[doc = "Field `CONNECTED` reader - connected \\[4\\]"]
pub type ConnectedR = crate::BitReader;
#[doc = "Field `RESET_ACTIVE` reader - reset_active \\[5\\]"]
pub type ResetActiveR = crate::BitReader;
#[doc = "Field `IDLE` reader - idle \\[6\\]"]
pub type IdleR = crate::BitReader;
#[doc = "Field `RESET_DONE` reader - reset_done \\[7\\]"]
pub type ResetDoneR = crate::BitReader;
impl R {
    #[doc = "Bits 0:1 - line_state \\[1:0\\]"]
    #[inline(always)]
    pub fn line_state(&self) -> LineStateR {
        LineStateR::new(self.bits & 3)
    }
    #[doc = "Bits 2:3 - speed \\[3:2\\]"]
    #[inline(always)]
    pub fn speed(&self) -> SpeedR {
        SpeedR::new((self.bits >> 2) & 3)
    }
    #[doc = "Bit 4 - connected \\[4\\]"]
    #[inline(always)]
    pub fn connected(&self) -> ConnectedR {
        ConnectedR::new(((self.bits >> 4) & 1) != 0)
    }
    #[doc = "Bit 5 - reset_active \\[5\\]"]
    #[inline(always)]
    pub fn reset_active(&self) -> ResetActiveR {
        ResetActiveR::new(((self.bits >> 5) & 1) != 0)
    }
    #[doc = "Bit 6 - idle \\[6\\]"]
    #[inline(always)]
    pub fn idle(&self) -> IdleR {
        IdleR::new(((self.bits >> 6) & 1) != 0)
    }
    #[doc = "Bit 7 - reset_done \\[7\\]"]
    #[inline(always)]
    pub fn reset_done(&self) -> ResetDoneR {
        ResetDoneR::new(((self.bits >> 7) & 1) != 0)
    }
}
#[doc = "USB_HOST.LINK, 8 bits at +0x05\n\nYou can [`read`](crate::Reg::read) this register and get [`link::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct LinkSpec;
impl crate::RegisterSpec for LinkSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`link::R`](R) reader structure"]
impl crate::Readable for LinkSpec {}
#[doc = "`reset()` method sets LINK to value 0"]
impl crate::Resettable for LinkSpec {}
