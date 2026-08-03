#[doc = "Register `CR_SR` reader"]
pub type R = crate::R<CrSrSpec>;
#[doc = "Register `CR_SR` writer"]
pub type W = crate::W<CrSrSpec>;
#[doc = "Field `DATA` reader - data \\[7:0\\]"]
pub type DataR = crate::FieldReader;
#[doc = "Field `DATA` writer - data \\[7:0\\]"]
pub type DataW<'a, REG> = crate::FieldWriter<'a, REG, 8>;
impl R {
    #[doc = "Bits 0:7 - data \\[7:0\\]"]
    #[inline(always)]
    pub fn data(&self) -> DataR {
        DataR::new(self.bits)
    }
}
impl W {
    #[doc = "Bits 0:7 - data \\[7:0\\]"]
    #[inline(always)]
    pub fn data(&mut self) -> DataW<'_, CrSrSpec> {
        DataW::new(self, 0)
    }
}
#[doc = "BOARD_I2C.CR_SR, 8 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`cr_sr::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`cr_sr::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct CrSrSpec;
impl crate::RegisterSpec for CrSrSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`cr_sr::R`](R) reader structure"]
impl crate::Readable for CrSrSpec {}
#[doc = "`write(|w| ..)` method takes [`cr_sr::W`](W) writer structure"]
impl crate::Writable for CrSrSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets CR_SR to value 0"]
impl crate::Resettable for CrSrSpec {}
