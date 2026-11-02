#[doc = "Register `TR_CNT_HI` reader"]
pub type R = crate::R<TrCntHiSpec>;
#[doc = "Field `V` reader - v \\[7:0\\]"]
pub type VR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - v \\[7:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "USB_HOST.TR_CNT_HI, 8 bits at +0x1b\n\nYou can [`read`](crate::Reg::read) this register and get [`tr_cnt_hi::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct TrCntHiSpec;
impl crate::RegisterSpec for TrCntHiSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`tr_cnt_hi::R`](R) reader structure"]
impl crate::Readable for TrCntHiSpec {}
#[doc = "`reset()` method sets TR_CNT_HI to value 0"]
impl crate::Resettable for TrCntHiSpec {}
