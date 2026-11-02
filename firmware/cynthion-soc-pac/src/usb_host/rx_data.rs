#[doc = "Register `RX_DATA` reader"]
pub type R = crate::R<RxDataSpec>;
#[doc = "Register `RX_DATA` writer"]
pub type W = crate::W<RxDataSpec>;
#[doc = "Field `V` reader - v \\[7:0\\]"]
pub type VR = crate::FieldReader;
#[doc = "Field `V` writer - v \\[7:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 8>;
impl R {
    #[doc = "Bits 0:7 - v \\[7:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
impl W {
    #[doc = "Bits 0:7 - v \\[7:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, RxDataSpec> {
        VW::new(self, 0)
    }
}
#[doc = "USB_HOST.RX_DATA, 8 bits at +0x10\n\nYou can [`read`](crate::Reg::read) this register and get [`rx_data::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`rx_data::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RxDataSpec;
impl crate::RegisterSpec for RxDataSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`rx_data::R`](R) reader structure"]
impl crate::Readable for RxDataSpec {}
#[doc = "`write(|w| ..)` method takes [`rx_data::W`](W) writer structure"]
impl crate::Writable for RxDataSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets RX_DATA to value 0"]
impl crate::Resettable for RxDataSpec {}
