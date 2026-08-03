#[doc = "Register `ADDRESS` reader"]
pub type R = crate::R<AddressSpec>;
#[doc = "Register `ADDRESS` writer"]
pub type W = crate::W<AddressSpec>;
#[doc = "Field `ADDRESS` reader - address \\[5:0\\]"]
pub type AddressR = crate::FieldReader;
#[doc = "Field `ADDRESS` writer - address \\[5:0\\]"]
pub type AddressW<'a, REG> = crate::FieldWriter<'a, REG, 6>;
impl R {
    #[doc = "Bits 0:5 - address \\[5:0\\]"]
    #[inline(always)]
    pub fn address(&self) -> AddressR {
        AddressR::new(self.bits & 0x3f)
    }
}
impl W {
    #[doc = "Bits 0:5 - address \\[5:0\\]"]
    #[inline(always)]
    pub fn address(&mut self) -> AddressW<'_, AddressSpec> {
        AddressW::new(self, 0)
    }
}
#[doc = "BOARD_ULPI.ADDRESS, 6 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`address::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`address::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct AddressSpec;
impl crate::RegisterSpec for AddressSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`address::R`](R) reader structure"]
impl crate::Readable for AddressSpec {}
#[doc = "`write(|w| ..)` method takes [`address::W`](W) writer structure"]
impl crate::Writable for AddressSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets ADDRESS to value 0"]
impl crate::Resettable for AddressSpec {}
