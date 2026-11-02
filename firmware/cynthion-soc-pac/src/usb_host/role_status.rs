#[doc = "Register `ROLE_STATUS` reader"]
pub type R = crate::R<RoleStatusSpec>;
#[doc = "Field `ROLE` reader - role \\[1:0\\]"]
pub type RoleR = crate::FieldReader;
#[doc = "Field `SWITCHING` reader - switching \\[2\\]"]
pub type SwitchingR = crate::BitReader;
#[doc = "Field `TIMEOUT` reader - timeout \\[3\\]"]
pub type TimeoutR = crate::BitReader;
impl R {
    #[doc = "Bits 0:1 - role \\[1:0\\]"]
    #[inline(always)]
    pub fn role(&self) -> RoleR {
        RoleR::new(self.bits & 3)
    }
    #[doc = "Bit 2 - switching \\[2\\]"]
    #[inline(always)]
    pub fn switching(&self) -> SwitchingR {
        SwitchingR::new(((self.bits >> 2) & 1) != 0)
    }
    #[doc = "Bit 3 - timeout \\[3\\]"]
    #[inline(always)]
    pub fn timeout(&self) -> TimeoutR {
        TimeoutR::new(((self.bits >> 3) & 1) != 0)
    }
}
#[doc = "USB_HOST.ROLE_STATUS, 4 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`role_status::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RoleStatusSpec;
impl crate::RegisterSpec for RoleStatusSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`role_status::R`](R) reader structure"]
impl crate::Readable for RoleStatusSpec {}
#[doc = "`reset()` method sets ROLE_STATUS to value 0"]
impl crate::Resettable for RoleStatusSpec {}
