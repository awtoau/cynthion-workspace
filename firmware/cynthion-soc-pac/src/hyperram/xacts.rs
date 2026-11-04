#[doc = "Register `XACTS` reader"]
pub type R = crate::R<XactsSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.XACTS, 32 bits at +0x50\n\nYou can [`read`](crate::Reg::read) this register and get [`xacts::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct XactsSpec;
impl crate::RegisterSpec for XactsSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`xacts::R`](R) reader structure"]
impl crate::Readable for XactsSpec {}
#[doc = "`reset()` method sets XACTS to value 0"]
impl crate::Resettable for XactsSpec {}
