#[doc = "Register `RX` reader"]
pub type R = crate::R<RxSpec>;
#[doc = "Field `DATA` reader - data \\[7:0\\]"]
pub type DataR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - data \\[7:0\\]"]
    #[inline(always)]
    pub fn data(&self) -> DataR {
        DataR::new(self.bits)
    }
}
#[doc = "BOARD_SIDEBAND.RX, 8 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`rx::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RxSpec;
impl crate::RegisterSpec for RxSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`rx::R`](R) reader structure"]
impl crate::Readable for RxSpec {}
#[doc = "`reset()` method sets RX to value 0"]
impl crate::Resettable for RxSpec {}
