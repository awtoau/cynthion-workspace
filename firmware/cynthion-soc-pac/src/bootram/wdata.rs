#[doc = "Register `WDATA` writer"]
pub type W = crate::W<WdataSpec>;
#[doc = "Field `DATA` writer - data \\[31:0\\]"]
pub type DataW<'a, REG> = crate::FieldWriter<'a, REG, 32, u32>;
impl W {
    #[doc = "Bits 0:31 - data \\[31:0\\]"]
    #[inline(always)]
    pub fn data(&mut self) -> DataW<'_, WdataSpec> {
        DataW::new(self, 0)
    }
}
#[doc = "BOOTRAM.WDATA, 32 bits at +0x14\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`wdata::W`](W). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct WdataSpec;
impl crate::RegisterSpec for WdataSpec {
    type Ux = u32;
}
#[doc = "`write(|w| ..)` method takes [`wdata::W`](W) writer structure"]
impl crate::Writable for WdataSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets WDATA to value 0"]
impl crate::Resettable for WdataSpec {}
