#[doc = "Register `LINES` reader"]
pub type R = crate::R<LinesSpec>;
#[doc = "Field `TARGET_INT` reader - target_int \\[0\\]"]
pub type TargetIntR = crate::BitReader;
#[doc = "Field `AUX_INT` reader - aux_int \\[1\\]"]
pub type AuxIntR = crate::BitReader;
#[doc = "Field `TARGET_FAULT` reader - target_fault \\[2\\]"]
pub type TargetFaultR = crate::BitReader;
#[doc = "Field `AUX_FAULT` reader - aux_fault \\[3\\]"]
pub type AuxFaultR = crate::BitReader;
impl R {
    #[doc = "Bit 0 - target_int \\[0\\]"]
    #[inline(always)]
    pub fn target_int(&self) -> TargetIntR {
        TargetIntR::new((self.bits & 1) != 0)
    }
    #[doc = "Bit 1 - aux_int \\[1\\]"]
    #[inline(always)]
    pub fn aux_int(&self) -> AuxIntR {
        AuxIntR::new(((self.bits >> 1) & 1) != 0)
    }
    #[doc = "Bit 2 - target_fault \\[2\\]"]
    #[inline(always)]
    pub fn target_fault(&self) -> TargetFaultR {
        TargetFaultR::new(((self.bits >> 2) & 1) != 0)
    }
    #[doc = "Bit 3 - aux_fault \\[3\\]"]
    #[inline(always)]
    pub fn aux_fault(&self) -> AuxFaultR {
        AuxFaultR::new(((self.bits >> 3) & 1) != 0)
    }
}
#[doc = "BOARD_I2C_MUX.LINES, 4 bits at +0x01\n\nYou can [`read`](crate::Reg::read) this register and get [`lines::R`](R). See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct LinesSpec;
impl crate::RegisterSpec for LinesSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`lines::R`](R) reader structure"]
impl crate::Readable for LinesSpec {}
#[doc = "`reset()` method sets LINES to value 0"]
impl crate::Resettable for LinesSpec {}
