#[doc = "Register `REG_RDATA` reader"]
pub type R = crate::R<RegRdataSpec>;
#[doc = "Field `V` reader - v \\[15:0\\]"]
pub type VR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - v \\[15:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.REG_RDATA, 16 bits at +0x30\n\nYou can [`read`](crate::Reg::read) this register and get [`reg_rdata::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RegRdataSpec;
impl crate::RegisterSpec for RegRdataSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`reg_rdata::R`](R) reader structure"]
impl crate::Readable for RegRdataSpec {}
#[doc = "`reset()` method sets REG_RDATA to value 0"]
impl crate::Resettable for RegRdataSpec {}
