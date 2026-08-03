#[doc = "Register `DATA_LO` reader"]
pub type R = crate::R<DataLoSpec>;
#[doc = "Field `DATA` reader - data \\[7:0\\]"]
pub type DataR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:7 - data \\[7:0\\]"]
    #[inline(always)]
    pub fn data(&self) -> DataR {
        DataR::new(self.bits)
    }
}
#[doc = "BOOTRAM.DATA_LO, 8 bits at +0x0d\n\nYou can [`read`](crate::Reg::read) this register and get [`data_lo::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct DataLoSpec;
impl crate::RegisterSpec for DataLoSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`data_lo::R`](R) reader structure"]
impl crate::Readable for DataLoSpec {}
#[doc = "`reset()` method sets DATA_LO to value 0"]
impl crate::Resettable for DataLoSpec {}
