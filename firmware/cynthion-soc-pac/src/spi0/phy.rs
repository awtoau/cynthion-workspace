#[doc = "Register `PHY` reader"]
pub type R = crate::R<PhySpec>;
#[doc = "Register `PHY` writer"]
pub type W = crate::W<PhySpec>;
#[doc = "Field `LENGTH` reader - length \\[5:0\\]"]
pub type LengthR = crate::FieldReader;
#[doc = "Field `LENGTH` writer - length \\[5:0\\]"]
pub type LengthW<'a, REG> = crate::FieldWriter<'a, REG, 6>;
#[doc = "Field `WIDTH` reader - width \\[9:6\\]"]
pub type WidthR = crate::FieldReader;
#[doc = "Field `WIDTH` writer - width \\[9:6\\]"]
pub type WidthW<'a, REG> = crate::FieldWriter<'a, REG, 4>;
#[doc = "Field `MASK` reader - mask \\[17:10\\]"]
pub type MaskR = crate::FieldReader;
#[doc = "Field `MASK` writer - mask \\[17:10\\]"]
pub type MaskW<'a, REG> = crate::FieldWriter<'a, REG, 8>;
impl R {
    #[doc = "Bits 0:5 - length \\[5:0\\]"]
    #[inline(always)]
    pub fn length(&self) -> LengthR {
        LengthR::new((self.bits & 0x3f) as u8)
    }
    #[doc = "Bits 6:9 - width \\[9:6\\]"]
    #[inline(always)]
    pub fn width(&self) -> WidthR {
        WidthR::new(((self.bits >> 6) & 0x0f) as u8)
    }
    #[doc = "Bits 10:17 - mask \\[17:10\\]"]
    #[inline(always)]
    pub fn mask(&self) -> MaskR {
        MaskR::new(((self.bits >> 10) & 0xff) as u8)
    }
}
impl W {
    #[doc = "Bits 0:5 - length \\[5:0\\]"]
    #[inline(always)]
    pub fn length(&mut self) -> LengthW<'_, PhySpec> {
        LengthW::new(self, 0)
    }
    #[doc = "Bits 6:9 - width \\[9:6\\]"]
    #[inline(always)]
    pub fn width(&mut self) -> WidthW<'_, PhySpec> {
        WidthW::new(self, 6)
    }
    #[doc = "Bits 10:17 - mask \\[17:10\\]"]
    #[inline(always)]
    pub fn mask(&mut self) -> MaskW<'_, PhySpec> {
        MaskW::new(self, 10)
    }
}
#[doc = "SPI0.PHY, 18 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`phy::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`phy::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct PhySpec;
impl crate::RegisterSpec for PhySpec {
    type Ux = u32;
}
#[doc = "`read()` method returns [`phy::R`](R) reader structure"]
impl crate::Readable for PhySpec {}
#[doc = "`write(|w| ..)` method takes [`phy::W`](W) writer structure"]
impl crate::Writable for PhySpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets PHY to value 0"]
impl crate::Resettable for PhySpec {}
