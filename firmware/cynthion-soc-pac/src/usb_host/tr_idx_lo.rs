#[doc = "Register `TR_IDX_LO` reader"]
pub type R = crate::R<TrIdxLoSpec>;
#[doc = "Register `TR_IDX_LO` writer"]
pub type W = crate::W<TrIdxLoSpec>;
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
    pub fn v(&mut self) -> VW<'_, TrIdxLoSpec> {
        VW::new(self, 0)
    }
}
#[doc = "USB_HOST.TR_IDX_LO, 8 bits at +0x1c\n\nYou can [`read`](crate::Reg::read) this register and get [`tr_idx_lo::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tr_idx_lo::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct TrIdxLoSpec;
impl crate::RegisterSpec for TrIdxLoSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`tr_idx_lo::R`](R) reader structure"]
impl crate::Readable for TrIdxLoSpec {}
#[doc = "`write(|w| ..)` method takes [`tr_idx_lo::W`](W) writer structure"]
impl crate::Writable for TrIdxLoSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets TR_IDX_LO to value 0"]
impl crate::Resettable for TrIdxLoSpec {}
