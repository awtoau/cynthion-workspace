#[doc = "Register `ADDR_RD` reader"]
pub type R = crate::R<AddrRdSpec>;
#[doc = "Field `ADDR` reader - addr \\[31:0\\]"]
pub type AddrR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - addr \\[31:0\\]"]
    #[inline(always)]
    pub fn addr(&self) -> AddrR {
        AddrR::new(self.bits)
    }
}
#[doc = "BOOTRAM.ADDR_RD, 32 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`addr_rd::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct AddrRdSpec;
impl crate::RegisterSpec for AddrRdSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`addr_rd::R`](R) reader structure"]
impl crate::Readable for AddrRdSpec {}
#[doc = "`reset()` method sets ADDR_RD to value 0"]
impl crate::Resettable for AddrRdSpec {}
