#[doc = "Register `SCK_KHZ` reader"]
pub type R = crate::R<SckKhzSpec>;
#[doc = "Field `V` reader - v \\[31:0\\]"]
pub type VR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - v \\[31:0\\]"]
    #[inline(always)]
    pub fn v(&self) -> VR {
        VR::new(self.bits)
    }
}
#[doc = "SPI0.SCK_KHZ, 32 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`sck_khz::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct SckKhzSpec;
impl crate::RegisterSpec for SckKhzSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`sck_khz::R`](R) reader structure"]
impl crate::Readable for SckKhzSpec {}
#[doc = "`reset()` method sets SCK_KHZ to value 0"]
impl crate::Resettable for SckKhzSpec {}
