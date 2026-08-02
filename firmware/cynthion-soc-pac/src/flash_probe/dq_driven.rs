#[doc = "Register `DQ_DRIVEN` reader"]
pub type R = crate::R<DqDrivenSpec>;
#[doc = "Field `SEEN` reader - seen \\[0\\]"]
pub type SeenR = crate::BitReader;
impl R {
    #[doc = "Bit 0 - seen \\[0\\]"]
    #[inline(always)]
    pub fn seen(&self) -> SeenR {
        SeenR::new((self.bits & 1) != 0)
    }
}
#[doc = "FLASH_PROBE.DQ_DRIVEN, 1 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`dq_driven::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct DqDrivenSpec;
impl crate::RegisterSpec for DqDrivenSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`dq_driven::R`](R) reader structure"]
impl crate::Readable for DqDrivenSpec {}
#[doc = "`reset()` method sets DQ_DRIVEN to value 0"]
impl crate::Resettable for DqDrivenSpec {}
