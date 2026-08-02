#[doc = "Register `TX` reader"]
pub type R = crate::R<TxSpec>;
#[doc = "Register `TX` writer"]
pub type W = crate::W<TxSpec>;
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
    pub fn data(&mut self) -> DataW<'_, TxSpec> {
        DataW::new(self, 0)
    }
}
#[doc = "BOARD_SIDEBAND.TX, 8 bits at +0x01\n\nYou can [`read`](crate::Reg::read) this register and get [`tx::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tx::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct TxSpec;
impl crate::RegisterSpec for TxSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`tx::R`](R) reader structure"]
impl crate::Readable for TxSpec {}
#[doc = "`write(|w| ..)` method takes [`tx::W`](W) writer structure"]
impl crate::Writable for TxSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets TX to value 0"]
impl crate::Resettable for TxSpec {}
