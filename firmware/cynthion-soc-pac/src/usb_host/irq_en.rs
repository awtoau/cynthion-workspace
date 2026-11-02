#[doc = "Register `IRQ_EN` reader"]
pub type R = crate::R<IrqEnSpec>;
#[doc = "Register `IRQ_EN` writer"]
pub type W = crate::W<IrqEnSpec>;
#[doc = "Field `V` reader - v \\[3:0\\]"]
pub type VR = crate::FieldReader;
#[doc = "Field `V` writer - v \\[3:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 4>;
impl R {
    #[doc = "Bits 0:3 - v \\[3:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 0x0f)
    }
}
impl W {
    #[doc = "Bits 0:3 - v \\[3:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, IrqEnSpec> {
        VW::new(self, 0)
    }
}
#[doc = "USB_HOST.IRQ_EN, 4 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`irq_en::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`irq_en::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct IrqEnSpec;
impl crate::RegisterSpec for IrqEnSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`irq_en::R`](R) reader structure"]
impl crate::Readable for IrqEnSpec {}
#[doc = "`write(|w| ..)` method takes [`irq_en::W`](W) writer structure"]
impl crate::Writable for IrqEnSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets IRQ_EN to value 0"]
impl crate::Resettable for IrqEnSpec {}
