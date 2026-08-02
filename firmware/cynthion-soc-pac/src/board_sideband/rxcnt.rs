#[doc = "Register `RXCNT` reader"]
pub type R = crate::R<RxcntSpec>;
#[doc = "Field `DATA` reader - data \\[7:0\\]"]
pub type DataR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - data \\[7:0\\]"]
    #[inline(always)]
    pub fn data(&self) -> DataR {
        DataR::new(self.bits)
    }
}
#[doc = "BOARD_SIDEBAND.RXCNT, 8 bits at +0x03\n\nYou can [`read`](crate::Reg::read) this register and get [`rxcnt::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RxcntSpec;
impl crate::RegisterSpec for RxcntSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`rxcnt::R`](R) reader structure"]
impl crate::Readable for RxcntSpec {}
#[doc = "`reset()` method sets RXCNT to value 0"]
impl crate::Resettable for RxcntSpec {}
