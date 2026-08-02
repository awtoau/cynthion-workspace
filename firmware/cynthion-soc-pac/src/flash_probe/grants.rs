#[doc = "Register `GRANTS` reader"]
pub type R = crate::R<GrantsSpec>;
#[doc = "Field `COUNT` reader - count \\[15:0\\]"]
pub type CountR = crate::FieldReader<u16>;
impl R {
    #[doc = "Bits 0:15 - count \\[15:0\\]"]
    #[inline(always)]
    pub fn count(&self) -> CountR {
        CountR::new(self.bits)
    }
}
#[doc = "FLASH_PROBE.GRANTS, 16 bits at +0x06\n\nYou can [`read`](crate::Reg::read) this register and get [`grants::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct GrantsSpec;
impl crate::RegisterSpec for GrantsSpec {
    type Ux = u16;
}
#[doc = "`read()` method returns [`grants::R`](R) reader structure"]
impl crate::Readable for GrantsSpec {}
#[doc = "`reset()` method sets GRANTS to value 0"]
impl crate::Resettable for GrantsSpec {}
