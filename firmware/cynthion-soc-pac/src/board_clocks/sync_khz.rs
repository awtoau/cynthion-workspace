#[doc = "Register `SYNC_KHZ` reader"]
pub type R = crate::R<SyncKhzSpec>;
#[doc = "Field `VALUE` reader - value \\[31:0\\]"]
pub type ValueR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - value \\[31:0\\]"]
    #[inline(always)]
    pub fn value(&self) -> ValueR {
        ValueR::new(self.bits)
    }
}
#[doc = "BOARD_CLOCKS.SYNC_KHZ, 32 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`sync_khz::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct SyncKhzSpec;
impl crate::RegisterSpec for SyncKhzSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`sync_khz::R`](R) reader structure"]
impl crate::Readable for SyncKhzSpec {}
#[doc = "`reset()` method sets SYNC_KHZ to value 0"]
impl crate::Resettable for SyncKhzSpec {}
