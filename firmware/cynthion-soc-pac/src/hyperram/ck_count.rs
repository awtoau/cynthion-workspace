#[doc = "Register `CK_COUNT` reader"]
pub type R = crate::R<CkCountSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.CK_COUNT, 32 bits at +0x6c\n\nYou can [`read`](crate::Reg::read) this register and get [`ck_count::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CkCountSpec;
impl crate::RegisterSpec for CkCountSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`ck_count::R`](R) reader structure"]
impl crate::Readable for CkCountSpec {}
#[doc = "`reset()` method sets CK_COUNT to value 0"]
impl crate::Resettable for CkCountSpec {}
