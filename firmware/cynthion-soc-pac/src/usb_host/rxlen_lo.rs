#[doc = "Register `RXLEN_LO` reader"]
pub type R = crate::R<RxlenLoSpec>;
#[doc = "Field `V` reader - v \\[7:0\\]"]
pub type VR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - v \\[7:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "USB_HOST.RXLEN_LO, 8 bits at +0x0c\n\nYou can [`read`](crate::Reg::read) this register and get [`rxlen_lo::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RxlenLoSpec;
impl crate::RegisterSpec for RxlenLoSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`rxlen_lo::R`](R) reader structure"]
impl crate::Readable for RxlenLoSpec {}
#[doc = "`reset()` method sets RXLEN_LO to value 0"]
impl crate::Resettable for RxlenLoSpec {}
