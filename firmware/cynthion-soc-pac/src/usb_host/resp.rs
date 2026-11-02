#[doc = "Register `RESP` reader"]
pub type R = crate::R<RespSpec>;
#[doc = "Field `RESPONSE` reader - response \\[2:0\\]"]
pub type ResponseR = crate::FieldReader;
#[doc = "Field `NYET` reader - nyet \\[3\\]"]
pub type NyetR = crate::BitReader;
impl R {
    #[doc = "Bits 0:2 - response \\[2:0\\]"]
    #[inline(always)]
    pub fn response(&self) -> ResponseR {
        ResponseR::new(self.bits & 7)
    }
    #[doc = "Bit 3 - nyet \\[3\\]"]
    #[inline(always)]
    pub fn nyet(&self) -> NyetR {
        NyetR::new(((self.bits >> 3) & 1) != 0)
    }
}
#[doc = "USB_HOST.RESP, 4 bits at +0x0a\n\nYou can [`read`](crate::Reg::read) this register and get [`resp::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct RespSpec;
impl crate::RegisterSpec for RespSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`resp::R`](R) reader structure"]
impl crate::Readable for RespSpec {}
#[doc = "`reset()` method sets RESP to value 0"]
impl crate::Resettable for RespSpec {}
