#[doc = "Register `CPU` reader"]
pub type R = crate::R<CpuSpec>;
#[doc = "Field `VALUE` reader - value \\[31:0\\]"]
pub type ValueR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - value \\[31:0\\]"]
    #[inline(always)]
    pub fn value(&self) -> ValueR {
        ValueR::new(self.bits)
    }
}
#[doc = "BOARD_GATEWARE.CPU, 32 bits at +0x10\n\nYou can [`read`](crate::Reg::read) this register and get [`cpu::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CpuSpec;
impl crate::RegisterSpec for CpuSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`cpu::R`](R) reader structure"]
impl crate::Readable for CpuSpec {}
#[doc = "`reset()` method sets CPU to value 0"]
impl crate::Resettable for CpuSpec {}
