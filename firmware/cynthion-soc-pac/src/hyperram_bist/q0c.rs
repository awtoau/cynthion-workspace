#[doc = "Register `Q0C` reader"]
pub type R = crate::R<Q0cSpec>;
#[doc = "Field `W` reader - w \\[31:0\\]"]
pub type WR = crate::FieldReader<u32>;
impl R {
    #[doc = "Bits 0:31 - w \\[31:0\\]"]
    #[inline(always)]
    pub fn w(&self) -> WR {
        WR::new(self.bits)
    }
}
#[doc = "HYPERRAM_BIST.Q0C, 32 bits at +0x130\n\nYou can [`read`](crate::Reg::read) this register and get [`q0c::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct Q0cSpec;
impl crate::RegisterSpec for Q0cSpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`q0c::R`](R) reader structure"]
impl crate::Readable for Q0cSpec {}
#[doc = "`reset()` method sets Q0C to value 0"]
impl crate::Resettable for Q0cSpec {}
