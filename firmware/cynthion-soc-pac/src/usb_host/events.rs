#[doc = "Register `EVENTS` reader"]
pub type R = crate::R<EventsSpec>;
#[doc = "Register `EVENTS` writer"]
pub type W = crate::W<EventsSpec>;
#[doc = "Field `EV` reader - ev \\[7:0\\]"]
pub type EvR = crate::FieldReader;
#[doc = "Field `EV` writer - ev \\[7:0\\]"]
pub type EvW<'a, REG> = crate::FieldWriter<'a, REG, 8>;
impl R {
    #[doc = "Bits 0:7 - ev \\[7:0\\]"]
    #[inline(always)]
    pub fn ev(&self) -> EvR {
        EvR::new(self.bits)
    }
}
impl W {
    #[doc = "Bits 0:7 - ev \\[7:0\\]"]
    #[inline(always)]
    pub fn ev(&mut self) -> EvW<'_, EventsSpec> {
        EvW::new(self, 0)
    }
}
#[doc = "USB_HOST.EVENTS, 8 bits at +0x06\n\nYou can [`read`](crate::Reg::read) this register and get [`events::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`events::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct EventsSpec;
impl crate::RegisterSpec for EventsSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`events::R`](R) reader structure"]
impl crate::Readable for EventsSpec {}
#[doc = "`write(|w| ..)` method takes [`events::W`](W) writer structure"]
impl crate::Writable for EventsSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets EVENTS to value 0"]
impl crate::Resettable for EventsSpec {}
