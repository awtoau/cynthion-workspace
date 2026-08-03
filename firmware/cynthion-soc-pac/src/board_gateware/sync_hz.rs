#[doc = "Register `SYNC_HZ` reader"]
pub type R = crate::R<SyncHzSpec>;
#[doc = "Field `VALUE` reader - value \\[31:0\\]"]
pub type ValueR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - value \\[31:0\\]"]
    #[inline(always)]
    pub fn value(&self) -> ValueR {
        ValueR::new(self.bits)
    }
}
#[doc = "BOARD_GATEWARE.SYNC_HZ, 32 bits at +0x0c\n\nYou can [`read`](crate::Reg::read) this register and get [`sync_hz::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct SyncHzSpec;
impl crate::RegisterSpec for SyncHzSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`sync_hz::R`](R) reader structure"]
impl crate::Readable for SyncHzSpec {}
#[doc = "`reset()` method sets SYNC_HZ to value 0"]
impl crate::Resettable for SyncHzSpec {}
