#[doc = "Register `TR_CNT_LO` reader"]
pub type R = crate::R<TrCntLoSpec>;
#[doc = "Field `V` reader - v \\[7:0\\]"]
pub type VR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - v \\[7:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "USB_HOST.TR_CNT_LO, 8 bits at +0x1a\n\nYou can [`read`](crate::Reg::read) this register and get [`tr_cnt_lo::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct TrCntLoSpec;
impl crate::RegisterSpec for TrCntLoSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`tr_cnt_lo::R`](R) reader structure"]
impl crate::Readable for TrCntLoSpec {}
#[doc = "`reset()` method sets TR_CNT_LO to value 0"]
impl crate::Resettable for TrCntLoSpec {}
