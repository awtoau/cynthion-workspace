#[doc = "Register `RBR_THR` reader"]
pub type R = crate::R<RbrThrSpec>;
#[doc = "Register `RBR_THR` writer"]
pub type W = crate::W<RbrThrSpec>;
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
    pub fn data(&mut self) -> DataW<'_, RbrThrSpec> {
        DataW::new(self, 0)
    }
}
#[doc = "CONSOLE.RBR_THR, 8 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`rbr_thr::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`rbr_thr::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RbrThrSpec;
impl crate::RegisterSpec for RbrThrSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`rbr_thr::R`](R) reader structure"]
impl crate::Readable for RbrThrSpec {}
#[doc = "`write(|w| ..)` method takes [`rbr_thr::W`](W) writer structure"]
impl crate::Writable for RbrThrSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets RBR_THR to value 0"]
impl crate::Resettable for RbrThrSpec {}
