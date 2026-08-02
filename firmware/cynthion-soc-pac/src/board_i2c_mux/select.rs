#[doc = "Register `SELECT` reader"]
pub type R = crate::R<SelectSpec>;
#[doc = "Register `SELECT` writer"]
pub type W = crate::W<SelectSpec>;
#[doc = "Field `SELECT` reader - select \\[1:0\\]"]
pub type SelectR = crate::FieldReader;
#[doc = "Field `SELECT` writer - select \\[1:0\\]"]
pub type SelectW<'a, REG> = crate::FieldWriter<'a, REG, 2>;
impl R {
    #[doc = "Bits 0:1 - select \\[1:0\\]"]
    #[inline(always)]
    pub fn select(&self) -> SelectR {
        SelectR::new(self.bits & 3)
    }
}
impl W {
    #[doc = "Bits 0:1 - select \\[1:0\\]"]
    #[inline(always)]
    pub fn select(&mut self) -> SelectW<'_, SelectSpec> {
        SelectW::new(self, 0)
    }
}
#[doc = "BOARD_I2C_MUX.SELECT, 2 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`select::R`](R). You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`select::W`](W). You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api)."]
pub struct SelectSpec;
impl crate::RegisterSpec for SelectSpec {
    type Ux = u8;
}
#[doc = "`read()` method returns [`select::R`](R) reader structure"]
impl crate::Readable for SelectSpec {}
#[doc = "`write(|w| ..)` method takes [`select::W`](W) writer structure"]
impl crate::Writable for SelectSpec {
    type Safety = crate::Unsafe;
}
#[doc = "`reset()` method sets SELECT to value 0x02"]
impl crate::Resettable for SelectSpec {
    const RESET_VALUE: u8 = 0x02;
}
