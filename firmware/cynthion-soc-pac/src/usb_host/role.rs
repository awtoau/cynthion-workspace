#[doc = "Register `ROLE` reader"]
pub type R = crate::R<RoleSpec>;
#[doc = "Register `ROLE` writer"]
pub type W = crate::W<RoleSpec>;
#[doc = "Field `V` reader - v \\[1:0\\]"]
pub type VR = crate::FieldReader;
#[doc = "Field `V` writer - v \\[1:0\\]"]
pub type VW<'a, REG> = crate::FieldWriter<'a, REG, 2>;
impl R {
    #[doc = "Bits 0:1 - v \\[1:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits & 3)
    }
}
impl W {
    #[doc = "Bits 0:1 - v \\[1:0\\]"]
    #[inline(always)]
    pub fn v(&mut self) -> VW<'_, RoleSpec> {
        VW::new(self, 0)
    }
}
#[doc = "USB_HOST.ROLE, 2 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`role::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`role::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RoleSpec;
impl crate::RegisterSpec for RoleSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`role::R`](R) reader structure"]
impl crate::Readable for RoleSpec {}
#[doc = "`write(|w| ..)` method takes [`role::W`](W) writer structure"]
impl crate::Writable for RoleSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets ROLE to value 0"]
impl crate::Resettable for RoleSpec {}
