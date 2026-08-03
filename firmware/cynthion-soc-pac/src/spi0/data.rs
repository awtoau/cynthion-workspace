#[doc = "Register `DATA` reader"]
pub type R = crate::R<DataSpec>;
#[doc = "Register `DATA` writer"]
pub type W = crate::W<DataSpec>;
#[doc = "Field `RX` reader - rx \\[31:0\\]"]
pub type RxR = crate::FieldReader<u32>;
#[doc = "Field `TX` writer - tx \\[63:32\\]"]
pub type TxW<'a, REG> = crate::FieldWriter<'a, REG, 32, u32>;
impl R {
    #[doc = "Bits 0:31 - rx \\[31:0\\]"]
    #[inline(always)]
    pub fn rx(&self) -> RxR {
        RxR::new((self.bits & 0xffff_ffff) as u32)
    }
}
impl W {
    #[doc = "Bits 32:63 - tx \\[63:32\\]"]
    #[inline(always)]
    pub fn tx(&mut self) -> TxW<'_, DataSpec> {
        TxW::new(self, 32)
    }
}
#[doc = "SPI0.DATA, 64 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`data::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`data::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct DataSpec;
impl crate::RegisterSpec for DataSpec {
    type Ux = u64;
}
#[doc = "`read()` method returns [`data::R`](R) reader structure"]
impl crate::Readable for DataSpec {}
#[doc = "`write(|w| ..)` method takes [`data::W`](W) writer structure"]
impl crate::Writable for DataSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets DATA to value 0"]
impl crate::Resettable for DataSpec {}
