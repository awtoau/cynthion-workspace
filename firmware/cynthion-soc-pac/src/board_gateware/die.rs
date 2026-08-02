#[doc = "Register `DIE` reader"]
pub type R = crate::R<DieSpec>;
#[doc = "Field `VALUE` reader - value \\[8:0\\]"]
pub type ValueR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:8 - value \\[8:0\\]"]
    #[inline(always)]
    pub fn value(&self) -> ValueR {
        ValueR::new(self.bits & 0x01ff)
    }
}
#[doc = "BOARD_GATEWARE.DIE, 9 bits at +0x18\n\nYou can [`read`](crate::Reg::read) this register and get [`die::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct DieSpec;
impl crate::RegisterSpec for DieSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`die::R`](R) reader structure"]
impl crate::Readable for DieSpec {}
#[doc = "`reset()` method sets DIE to value 0"]
impl crate::Resettable for DieSpec {}
