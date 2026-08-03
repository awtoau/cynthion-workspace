#[doc = "Register `PENDING` reader"]
pub type R = crate::R<PendingSpec>;
#[doc = "Field `BITS` reader - bits \\[5:0\\]"]
pub type BitsR = crate::FieldReader;
impl R {
    #[doc = "Bits 0:5 - bits \\[5:0\\]"]
    #[inline(always)]
    pub fn bits_(&self) -> BitsR {
        BitsR::new(self.bits & 0x3f)
    }
}
#[doc = "PLIC.PENDING, 6 bits at +0x1000\n\nYou can [`read`](crate::Reg::read) this register and get [`pending::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct PendingSpec;
impl crate::RegisterSpec for PendingSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`pending::R`](R) reader structure"]
impl crate::Readable for PendingSpec {}
#[doc = "`reset()` method sets PENDING to value 0"]
impl crate::Resettable for PendingSpec {}
