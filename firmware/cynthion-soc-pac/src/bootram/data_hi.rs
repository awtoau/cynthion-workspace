#[doc = "Register `DATA_HI` reader"]
pub type R = crate::R<DataHiSpec>;
#[doc = "Field `DATA` reader - data \\[7:0\\]"]
pub type DataR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - data \\[7:0\\]"]
    #[inline(always)]
    pub fn data(&self) -> DataR {
        DataR::new(self.bits)
    }
}
#[doc = "BOOTRAM.DATA_HI, 8 bits at +0x0b\n\nYou can [`read`](crate::Reg::read) this register and get [`data_hi::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct DataHiSpec;
impl crate::RegisterSpec for DataHiSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`data_hi::R`](R) reader structure"]
impl crate::Readable for DataHiSpec {}
#[doc = "`reset()` method sets DATA_HI to value 0"]
impl crate::Resettable for DataHiSpec {}
