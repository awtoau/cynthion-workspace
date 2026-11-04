#[doc = "Register `STATE` reader"]
pub type R = crate::R<StateSpec>;
#[doc = "Field `V` reader - v \\[15:0\\]"]
pub type VR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - v \\[15:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "HYPERRAM.STATE, 16 bits at +0x66\n\nYou can [`read`](crate::Reg::read) this register and get [`state::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct StateSpec;
impl crate::RegisterSpec for StateSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`state::R`](R) reader structure"]
impl crate::Readable for StateSpec {}
#[doc = "`reset()` method sets STATE to value 0"]
impl crate::Resettable for StateSpec {}
