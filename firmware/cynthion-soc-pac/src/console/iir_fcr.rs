#[doc = "Register `IIR_FCR` reader"]
pub type R = crate::R<IirFcrSpec>;
#[doc = "Register `IIR_FCR` writer"]
pub type W = crate::W<IirFcrSpec>;
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
    pub fn data(&mut self) -> DataW<'_, IirFcrSpec> {
        DataW::new(self, 0)
    }
}
#[doc = "CONSOLE.IIR_FCR, 8 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`iir_fcr::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`iir_fcr::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct IirFcrSpec;
impl crate::RegisterSpec for IirFcrSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`iir_fcr::R`](R) reader structure"]
impl crate::Readable for IirFcrSpec {}
#[doc = "`write(|w| ..)` method takes [`iir_fcr::W`](W) writer structure"]
impl crate::Writable for IirFcrSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets IIR_FCR to value 0"]
impl crate::Resettable for IirFcrSpec {}
