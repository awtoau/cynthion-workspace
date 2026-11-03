#[doc = "Register `CK_KHZ` reader"]
pub type R = crate::R<CkKhzSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.CK_KHZ, 32 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`ck_khz::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CkKhzSpec;
impl crate::RegisterSpec for CkKhzSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`ck_khz::R`](R) reader structure"]
impl crate::Readable for CkKhzSpec {}
#[doc = "`reset()` method sets CK_KHZ to value 0"]
impl crate::Resettable for CkKhzSpec {}
