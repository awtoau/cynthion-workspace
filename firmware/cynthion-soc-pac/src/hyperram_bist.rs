#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    p00: P00,
    p01: P01,
    p02: P02,
    p03: P03,
    p04: P04,
    p05: P05,
    p06: P06,
    p07: P07,
    p08: P08,
    p09: P09,
    p0a: P0a,
    p0b: P0b,
    p0c: P0c,
    p0d: P0d,
    p0e: P0e,
    p0f: P0f,
    p10: P10,
    p11: P11,
    p12: P12,
    p13: P13,
    p14: P14,
    p15: P15,
    p16: P16,
    p17: P17,
    p18: P18,
    p19: P19,
    p1a: P1a,
    p1b: P1b,
    p1c: P1c,
    p1d: P1d,
    p1e: P1e,
    p1f: P1f,
    _reserved32: [u8; 0x80],
    q00: Q00,
    q01: Q01,
    q02: Q02,
    q03: Q03,
    q04: Q04,
    q05: Q05,
    q06: Q06,
    q07: Q07,
    q08: Q08,
    q09: Q09,
    q0a: Q0a,
    q0b: Q0b,
    q0c: Q0c,
    q0d: Q0d,
    q0e: Q0e,
    q0f: Q0f,
    q10: Q10,
    q11: Q11,
    q12: Q12,
    q13: Q13,
    q14: Q14,
    q15: Q15,
    q16: Q16,
    q17: Q17,
    q18: Q18,
    q19: Q19,
    q1a: Q1a,
    q1b: Q1b,
    q1c: Q1c,
    q1d: Q1d,
    q1e: Q1e,
    q1f: Q1f,
}
impl RegisterBlock {
    #[doc = "0x00 - HYPERRAM_BIST.P00, 32 bits at +0x00"]
    #[inline(always)]
    pub const fn p00(&self) -> &P00 {
        &self.p00
    }
    #[doc = "0x04 - HYPERRAM_BIST.P01, 32 bits at +0x04"]
    #[inline(always)]
    pub const fn p01(&self) -> &P01 {
        &self.p01
    }
    #[doc = "0x08 - HYPERRAM_BIST.P02, 32 bits at +0x08"]
    #[inline(always)]
    pub const fn p02(&self) -> &P02 {
        &self.p02
    }
    #[doc = "0x0c - HYPERRAM_BIST.P03, 32 bits at +0x0c"]
    #[inline(always)]
    pub const fn p03(&self) -> &P03 {
        &self.p03
    }
    #[doc = "0x10 - HYPERRAM_BIST.P04, 32 bits at +0x10"]
    #[inline(always)]
    pub const fn p04(&self) -> &P04 {
        &self.p04
    }
    #[doc = "0x14 - HYPERRAM_BIST.P05, 32 bits at +0x14"]
    #[inline(always)]
    pub const fn p05(&self) -> &P05 {
        &self.p05
    }
    #[doc = "0x18 - HYPERRAM_BIST.P06, 32 bits at +0x18"]
    #[inline(always)]
    pub const fn p06(&self) -> &P06 {
        &self.p06
    }
    #[doc = "0x1c - HYPERRAM_BIST.P07, 32 bits at +0x1c"]
    #[inline(always)]
    pub const fn p07(&self) -> &P07 {
        &self.p07
    }
    #[doc = "0x20 - HYPERRAM_BIST.P08, 32 bits at +0x20"]
    #[inline(always)]
    pub const fn p08(&self) -> &P08 {
        &self.p08
    }
    #[doc = "0x24 - HYPERRAM_BIST.P09, 32 bits at +0x24"]
    #[inline(always)]
    pub const fn p09(&self) -> &P09 {
        &self.p09
    }
    #[doc = "0x28 - HYPERRAM_BIST.P0A, 32 bits at +0x28"]
    #[inline(always)]
    pub const fn p0a(&self) -> &P0a {
        &self.p0a
    }
    #[doc = "0x2c - HYPERRAM_BIST.P0B, 32 bits at +0x2c"]
    #[inline(always)]
    pub const fn p0b(&self) -> &P0b {
        &self.p0b
    }
    #[doc = "0x30 - HYPERRAM_BIST.P0C, 32 bits at +0x30"]
    #[inline(always)]
    pub const fn p0c(&self) -> &P0c {
        &self.p0c
    }
    #[doc = "0x34 - HYPERRAM_BIST.P0D, 32 bits at +0x34"]
    #[inline(always)]
    pub const fn p0d(&self) -> &P0d {
        &self.p0d
    }
    #[doc = "0x38 - HYPERRAM_BIST.P0E, 32 bits at +0x38"]
    #[inline(always)]
    pub const fn p0e(&self) -> &P0e {
        &self.p0e
    }
    #[doc = "0x3c - HYPERRAM_BIST.P0F, 32 bits at +0x3c"]
    #[inline(always)]
    pub const fn p0f(&self) -> &P0f {
        &self.p0f
    }
    #[doc = "0x40 - HYPERRAM_BIST.P10, 32 bits at +0x40"]
    #[inline(always)]
    pub const fn p10(&self) -> &P10 {
        &self.p10
    }
    #[doc = "0x44 - HYPERRAM_BIST.P11, 32 bits at +0x44"]
    #[inline(always)]
    pub const fn p11(&self) -> &P11 {
        &self.p11
    }
    #[doc = "0x48 - HYPERRAM_BIST.P12, 32 bits at +0x48"]
    #[inline(always)]
    pub const fn p12(&self) -> &P12 {
        &self.p12
    }
    #[doc = "0x4c - HYPERRAM_BIST.P13, 32 bits at +0x4c"]
    #[inline(always)]
    pub const fn p13(&self) -> &P13 {
        &self.p13
    }
    #[doc = "0x50 - HYPERRAM_BIST.P14, 32 bits at +0x50"]
    #[inline(always)]
    pub const fn p14(&self) -> &P14 {
        &self.p14
    }
    #[doc = "0x54 - HYPERRAM_BIST.P15, 32 bits at +0x54"]
    #[inline(always)]
    pub const fn p15(&self) -> &P15 {
        &self.p15
    }
    #[doc = "0x58 - HYPERRAM_BIST.P16, 32 bits at +0x58"]
    #[inline(always)]
    pub const fn p16(&self) -> &P16 {
        &self.p16
    }
    #[doc = "0x5c - HYPERRAM_BIST.P17, 32 bits at +0x5c"]
    #[inline(always)]
    pub const fn p17(&self) -> &P17 {
        &self.p17
    }
    #[doc = "0x60 - HYPERRAM_BIST.P18, 32 bits at +0x60"]
    #[inline(always)]
    pub const fn p18(&self) -> &P18 {
        &self.p18
    }
    #[doc = "0x64 - HYPERRAM_BIST.P19, 32 bits at +0x64"]
    #[inline(always)]
    pub const fn p19(&self) -> &P19 {
        &self.p19
    }
    #[doc = "0x68 - HYPERRAM_BIST.P1A, 32 bits at +0x68"]
    #[inline(always)]
    pub const fn p1a(&self) -> &P1a {
        &self.p1a
    }
    #[doc = "0x6c - HYPERRAM_BIST.P1B, 32 bits at +0x6c"]
    #[inline(always)]
    pub const fn p1b(&self) -> &P1b {
        &self.p1b
    }
    #[doc = "0x70 - HYPERRAM_BIST.P1C, 32 bits at +0x70"]
    #[inline(always)]
    pub const fn p1c(&self) -> &P1c {
        &self.p1c
    }
    #[doc = "0x74 - HYPERRAM_BIST.P1D, 32 bits at +0x74"]
    #[inline(always)]
    pub const fn p1d(&self) -> &P1d {
        &self.p1d
    }
    #[doc = "0x78 - HYPERRAM_BIST.P1E, 32 bits at +0x78"]
    #[inline(always)]
    pub const fn p1e(&self) -> &P1e {
        &self.p1e
    }
    #[doc = "0x7c - HYPERRAM_BIST.P1F, 32 bits at +0x7c"]
    #[inline(always)]
    pub const fn p1f(&self) -> &P1f {
        &self.p1f
    }
    #[doc = "0x100 - HYPERRAM_BIST.Q00, 32 bits at +0x100"]
    #[inline(always)]
    pub const fn q00(&self) -> &Q00 {
        &self.q00
    }
    #[doc = "0x104 - HYPERRAM_BIST.Q01, 32 bits at +0x104"]
    #[inline(always)]
    pub const fn q01(&self) -> &Q01 {
        &self.q01
    }
    #[doc = "0x108 - HYPERRAM_BIST.Q02, 32 bits at +0x108"]
    #[inline(always)]
    pub const fn q02(&self) -> &Q02 {
        &self.q02
    }
    #[doc = "0x10c - HYPERRAM_BIST.Q03, 32 bits at +0x10c"]
    #[inline(always)]
    pub const fn q03(&self) -> &Q03 {
        &self.q03
    }
    #[doc = "0x110 - HYPERRAM_BIST.Q04, 32 bits at +0x110"]
    #[inline(always)]
    pub const fn q04(&self) -> &Q04 {
        &self.q04
    }
    #[doc = "0x114 - HYPERRAM_BIST.Q05, 32 bits at +0x114"]
    #[inline(always)]
    pub const fn q05(&self) -> &Q05 {
        &self.q05
    }
    #[doc = "0x118 - HYPERRAM_BIST.Q06, 32 bits at +0x118"]
    #[inline(always)]
    pub const fn q06(&self) -> &Q06 {
        &self.q06
    }
    #[doc = "0x11c - HYPERRAM_BIST.Q07, 32 bits at +0x11c"]
    #[inline(always)]
    pub const fn q07(&self) -> &Q07 {
        &self.q07
    }
    #[doc = "0x120 - HYPERRAM_BIST.Q08, 32 bits at +0x120"]
    #[inline(always)]
    pub const fn q08(&self) -> &Q08 {
        &self.q08
    }
    #[doc = "0x124 - HYPERRAM_BIST.Q09, 32 bits at +0x124"]
    #[inline(always)]
    pub const fn q09(&self) -> &Q09 {
        &self.q09
    }
    #[doc = "0x128 - HYPERRAM_BIST.Q0A, 32 bits at +0x128"]
    #[inline(always)]
    pub const fn q0a(&self) -> &Q0a {
        &self.q0a
    }
    #[doc = "0x12c - HYPERRAM_BIST.Q0B, 32 bits at +0x12c"]
    #[inline(always)]
    pub const fn q0b(&self) -> &Q0b {
        &self.q0b
    }
    #[doc = "0x130 - HYPERRAM_BIST.Q0C, 32 bits at +0x130"]
    #[inline(always)]
    pub const fn q0c(&self) -> &Q0c {
        &self.q0c
    }
    #[doc = "0x134 - HYPERRAM_BIST.Q0D, 32 bits at +0x134"]
    #[inline(always)]
    pub const fn q0d(&self) -> &Q0d {
        &self.q0d
    }
    #[doc = "0x138 - HYPERRAM_BIST.Q0E, 32 bits at +0x138"]
    #[inline(always)]
    pub const fn q0e(&self) -> &Q0e {
        &self.q0e
    }
    #[doc = "0x13c - HYPERRAM_BIST.Q0F, 32 bits at +0x13c"]
    #[inline(always)]
    pub const fn q0f(&self) -> &Q0f {
        &self.q0f
    }
    #[doc = "0x140 - HYPERRAM_BIST.Q10, 32 bits at +0x140"]
    #[inline(always)]
    pub const fn q10(&self) -> &Q10 {
        &self.q10
    }
    #[doc = "0x144 - HYPERRAM_BIST.Q11, 32 bits at +0x144"]
    #[inline(always)]
    pub const fn q11(&self) -> &Q11 {
        &self.q11
    }
    #[doc = "0x148 - HYPERRAM_BIST.Q12, 32 bits at +0x148"]
    #[inline(always)]
    pub const fn q12(&self) -> &Q12 {
        &self.q12
    }
    #[doc = "0x14c - HYPERRAM_BIST.Q13, 32 bits at +0x14c"]
    #[inline(always)]
    pub const fn q13(&self) -> &Q13 {
        &self.q13
    }
    #[doc = "0x150 - HYPERRAM_BIST.Q14, 32 bits at +0x150"]
    #[inline(always)]
    pub const fn q14(&self) -> &Q14 {
        &self.q14
    }
    #[doc = "0x154 - HYPERRAM_BIST.Q15, 32 bits at +0x154"]
    #[inline(always)]
    pub const fn q15(&self) -> &Q15 {
        &self.q15
    }
    #[doc = "0x158 - HYPERRAM_BIST.Q16, 32 bits at +0x158"]
    #[inline(always)]
    pub const fn q16(&self) -> &Q16 {
        &self.q16
    }
    #[doc = "0x15c - HYPERRAM_BIST.Q17, 32 bits at +0x15c"]
    #[inline(always)]
    pub const fn q17(&self) -> &Q17 {
        &self.q17
    }
    #[doc = "0x160 - HYPERRAM_BIST.Q18, 32 bits at +0x160"]
    #[inline(always)]
    pub const fn q18(&self) -> &Q18 {
        &self.q18
    }
    #[doc = "0x164 - HYPERRAM_BIST.Q19, 32 bits at +0x164"]
    #[inline(always)]
    pub const fn q19(&self) -> &Q19 {
        &self.q19
    }
    #[doc = "0x168 - HYPERRAM_BIST.Q1A, 32 bits at +0x168"]
    #[inline(always)]
    pub const fn q1a(&self) -> &Q1a {
        &self.q1a
    }
    #[doc = "0x16c - HYPERRAM_BIST.Q1B, 32 bits at +0x16c"]
    #[inline(always)]
    pub const fn q1b(&self) -> &Q1b {
        &self.q1b
    }
    #[doc = "0x170 - HYPERRAM_BIST.Q1C, 32 bits at +0x170"]
    #[inline(always)]
    pub const fn q1c(&self) -> &Q1c {
        &self.q1c
    }
    #[doc = "0x174 - HYPERRAM_BIST.Q1D, 32 bits at +0x174"]
    #[inline(always)]
    pub const fn q1d(&self) -> &Q1d {
        &self.q1d
    }
    #[doc = "0x178 - HYPERRAM_BIST.Q1E, 32 bits at +0x178"]
    #[inline(always)]
    pub const fn q1e(&self) -> &Q1e {
        &self.q1e
    }
    #[doc = "0x17c - HYPERRAM_BIST.Q1F, 32 bits at +0x17c"]
    #[inline(always)]
    pub const fn q1f(&self) -> &Q1f {
        &self.q1f
    }
}
#[doc = "P00 (rw) register accessor: HYPERRAM_BIST.P00, 32 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`p00::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p00::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p00`] module"]
pub type P00 = crate::Reg<p00::P00Spec>;
#[doc = "HYPERRAM_BIST.P00, 32 bits at +0x00"]
pub mod p00;
#[doc = "P01 (rw) register accessor: HYPERRAM_BIST.P01, 32 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`p01::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p01::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p01`] module"]
pub type P01 = crate::Reg<p01::P01Spec>;
#[doc = "HYPERRAM_BIST.P01, 32 bits at +0x04"]
pub mod p01;
#[doc = "P02 (rw) register accessor: HYPERRAM_BIST.P02, 32 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`p02::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p02::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p02`] module"]
pub type P02 = crate::Reg<p02::P02Spec>;
#[doc = "HYPERRAM_BIST.P02, 32 bits at +0x08"]
pub mod p02;
#[doc = "P03 (rw) register accessor: HYPERRAM_BIST.P03, 32 bits at +0x0c\n\nYou can [`read`](crate::Reg::read) this register and get [`p03::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p03::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p03`] module"]
pub type P03 = crate::Reg<p03::P03Spec>;
#[doc = "HYPERRAM_BIST.P03, 32 bits at +0x0c"]
pub mod p03;
#[doc = "P04 (rw) register accessor: HYPERRAM_BIST.P04, 32 bits at +0x10\n\nYou can [`read`](crate::Reg::read) this register and get [`p04::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p04::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p04`] module"]
pub type P04 = crate::Reg<p04::P04Spec>;
#[doc = "HYPERRAM_BIST.P04, 32 bits at +0x10"]
pub mod p04;
#[doc = "P05 (rw) register accessor: HYPERRAM_BIST.P05, 32 bits at +0x14\n\nYou can [`read`](crate::Reg::read) this register and get [`p05::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p05::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p05`] module"]
pub type P05 = crate::Reg<p05::P05Spec>;
#[doc = "HYPERRAM_BIST.P05, 32 bits at +0x14"]
pub mod p05;
#[doc = "P06 (rw) register accessor: HYPERRAM_BIST.P06, 32 bits at +0x18\n\nYou can [`read`](crate::Reg::read) this register and get [`p06::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p06::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p06`] module"]
pub type P06 = crate::Reg<p06::P06Spec>;
#[doc = "HYPERRAM_BIST.P06, 32 bits at +0x18"]
pub mod p06;
#[doc = "P07 (rw) register accessor: HYPERRAM_BIST.P07, 32 bits at +0x1c\n\nYou can [`read`](crate::Reg::read) this register and get [`p07::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p07::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p07`] module"]
pub type P07 = crate::Reg<p07::P07Spec>;
#[doc = "HYPERRAM_BIST.P07, 32 bits at +0x1c"]
pub mod p07;
#[doc = "P08 (rw) register accessor: HYPERRAM_BIST.P08, 32 bits at +0x20\n\nYou can [`read`](crate::Reg::read) this register and get [`p08::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p08::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p08`] module"]
pub type P08 = crate::Reg<p08::P08Spec>;
#[doc = "HYPERRAM_BIST.P08, 32 bits at +0x20"]
pub mod p08;
#[doc = "P09 (rw) register accessor: HYPERRAM_BIST.P09, 32 bits at +0x24\n\nYou can [`read`](crate::Reg::read) this register and get [`p09::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p09::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p09`] module"]
pub type P09 = crate::Reg<p09::P09Spec>;
#[doc = "HYPERRAM_BIST.P09, 32 bits at +0x24"]
pub mod p09;
#[doc = "P0A (rw) register accessor: HYPERRAM_BIST.P0A, 32 bits at +0x28\n\nYou can [`read`](crate::Reg::read) this register and get [`p0a::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p0a::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p0a`] module"]
#[doc(alias = "P0A")]
pub type P0a = crate::Reg<p0a::P0aSpec>;
#[doc = "HYPERRAM_BIST.P0A, 32 bits at +0x28"]
pub mod p0a;
#[doc = "P0B (rw) register accessor: HYPERRAM_BIST.P0B, 32 bits at +0x2c\n\nYou can [`read`](crate::Reg::read) this register and get [`p0b::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p0b::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p0b`] module"]
#[doc(alias = "P0B")]
pub type P0b = crate::Reg<p0b::P0bSpec>;
#[doc = "HYPERRAM_BIST.P0B, 32 bits at +0x2c"]
pub mod p0b;
#[doc = "P0C (rw) register accessor: HYPERRAM_BIST.P0C, 32 bits at +0x30\n\nYou can [`read`](crate::Reg::read) this register and get [`p0c::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p0c::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p0c`] module"]
#[doc(alias = "P0C")]
pub type P0c = crate::Reg<p0c::P0cSpec>;
#[doc = "HYPERRAM_BIST.P0C, 32 bits at +0x30"]
pub mod p0c;
#[doc = "P0D (rw) register accessor: HYPERRAM_BIST.P0D, 32 bits at +0x34\n\nYou can [`read`](crate::Reg::read) this register and get [`p0d::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p0d::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p0d`] module"]
#[doc(alias = "P0D")]
pub type P0d = crate::Reg<p0d::P0dSpec>;
#[doc = "HYPERRAM_BIST.P0D, 32 bits at +0x34"]
pub mod p0d;
#[doc = "P0E (rw) register accessor: HYPERRAM_BIST.P0E, 32 bits at +0x38\n\nYou can [`read`](crate::Reg::read) this register and get [`p0e::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p0e::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p0e`] module"]
#[doc(alias = "P0E")]
pub type P0e = crate::Reg<p0e::P0eSpec>;
#[doc = "HYPERRAM_BIST.P0E, 32 bits at +0x38"]
pub mod p0e;
#[doc = "P0F (rw) register accessor: HYPERRAM_BIST.P0F, 32 bits at +0x3c\n\nYou can [`read`](crate::Reg::read) this register and get [`p0f::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p0f::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p0f`] module"]
#[doc(alias = "P0F")]
pub type P0f = crate::Reg<p0f::P0fSpec>;
#[doc = "HYPERRAM_BIST.P0F, 32 bits at +0x3c"]
pub mod p0f;
#[doc = "P10 (rw) register accessor: HYPERRAM_BIST.P10, 32 bits at +0x40\n\nYou can [`read`](crate::Reg::read) this register and get [`p10::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p10::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p10`] module"]
pub type P10 = crate::Reg<p10::P10Spec>;
#[doc = "HYPERRAM_BIST.P10, 32 bits at +0x40"]
pub mod p10;
#[doc = "P11 (rw) register accessor: HYPERRAM_BIST.P11, 32 bits at +0x44\n\nYou can [`read`](crate::Reg::read) this register and get [`p11::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p11::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p11`] module"]
pub type P11 = crate::Reg<p11::P11Spec>;
#[doc = "HYPERRAM_BIST.P11, 32 bits at +0x44"]
pub mod p11;
#[doc = "P12 (rw) register accessor: HYPERRAM_BIST.P12, 32 bits at +0x48\n\nYou can [`read`](crate::Reg::read) this register and get [`p12::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p12::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p12`] module"]
pub type P12 = crate::Reg<p12::P12Spec>;
#[doc = "HYPERRAM_BIST.P12, 32 bits at +0x48"]
pub mod p12;
#[doc = "P13 (rw) register accessor: HYPERRAM_BIST.P13, 32 bits at +0x4c\n\nYou can [`read`](crate::Reg::read) this register and get [`p13::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p13::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p13`] module"]
pub type P13 = crate::Reg<p13::P13Spec>;
#[doc = "HYPERRAM_BIST.P13, 32 bits at +0x4c"]
pub mod p13;
#[doc = "P14 (rw) register accessor: HYPERRAM_BIST.P14, 32 bits at +0x50\n\nYou can [`read`](crate::Reg::read) this register and get [`p14::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p14::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p14`] module"]
pub type P14 = crate::Reg<p14::P14Spec>;
#[doc = "HYPERRAM_BIST.P14, 32 bits at +0x50"]
pub mod p14;
#[doc = "P15 (rw) register accessor: HYPERRAM_BIST.P15, 32 bits at +0x54\n\nYou can [`read`](crate::Reg::read) this register and get [`p15::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p15::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p15`] module"]
pub type P15 = crate::Reg<p15::P15Spec>;
#[doc = "HYPERRAM_BIST.P15, 32 bits at +0x54"]
pub mod p15;
#[doc = "P16 (rw) register accessor: HYPERRAM_BIST.P16, 32 bits at +0x58\n\nYou can [`read`](crate::Reg::read) this register and get [`p16::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p16::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p16`] module"]
pub type P16 = crate::Reg<p16::P16Spec>;
#[doc = "HYPERRAM_BIST.P16, 32 bits at +0x58"]
pub mod p16;
#[doc = "P17 (rw) register accessor: HYPERRAM_BIST.P17, 32 bits at +0x5c\n\nYou can [`read`](crate::Reg::read) this register and get [`p17::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p17::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p17`] module"]
pub type P17 = crate::Reg<p17::P17Spec>;
#[doc = "HYPERRAM_BIST.P17, 32 bits at +0x5c"]
pub mod p17;
#[doc = "P18 (rw) register accessor: HYPERRAM_BIST.P18, 32 bits at +0x60\n\nYou can [`read`](crate::Reg::read) this register and get [`p18::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p18::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p18`] module"]
pub type P18 = crate::Reg<p18::P18Spec>;
#[doc = "HYPERRAM_BIST.P18, 32 bits at +0x60"]
pub mod p18;
#[doc = "P19 (rw) register accessor: HYPERRAM_BIST.P19, 32 bits at +0x64\n\nYou can [`read`](crate::Reg::read) this register and get [`p19::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p19::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p19`] module"]
pub type P19 = crate::Reg<p19::P19Spec>;
#[doc = "HYPERRAM_BIST.P19, 32 bits at +0x64"]
pub mod p19;
#[doc = "P1A (rw) register accessor: HYPERRAM_BIST.P1A, 32 bits at +0x68\n\nYou can [`read`](crate::Reg::read) this register and get [`p1a::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p1a::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p1a`] module"]
#[doc(alias = "P1A")]
pub type P1a = crate::Reg<p1a::P1aSpec>;
#[doc = "HYPERRAM_BIST.P1A, 32 bits at +0x68"]
pub mod p1a;
#[doc = "P1B (rw) register accessor: HYPERRAM_BIST.P1B, 32 bits at +0x6c\n\nYou can [`read`](crate::Reg::read) this register and get [`p1b::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p1b::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p1b`] module"]
#[doc(alias = "P1B")]
pub type P1b = crate::Reg<p1b::P1bSpec>;
#[doc = "HYPERRAM_BIST.P1B, 32 bits at +0x6c"]
pub mod p1b;
#[doc = "P1C (rw) register accessor: HYPERRAM_BIST.P1C, 32 bits at +0x70\n\nYou can [`read`](crate::Reg::read) this register and get [`p1c::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p1c::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p1c`] module"]
#[doc(alias = "P1C")]
pub type P1c = crate::Reg<p1c::P1cSpec>;
#[doc = "HYPERRAM_BIST.P1C, 32 bits at +0x70"]
pub mod p1c;
#[doc = "P1D (rw) register accessor: HYPERRAM_BIST.P1D, 32 bits at +0x74\n\nYou can [`read`](crate::Reg::read) this register and get [`p1d::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p1d::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p1d`] module"]
#[doc(alias = "P1D")]
pub type P1d = crate::Reg<p1d::P1dSpec>;
#[doc = "HYPERRAM_BIST.P1D, 32 bits at +0x74"]
pub mod p1d;
#[doc = "P1E (rw) register accessor: HYPERRAM_BIST.P1E, 32 bits at +0x78\n\nYou can [`read`](crate::Reg::read) this register and get [`p1e::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p1e::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p1e`] module"]
#[doc(alias = "P1E")]
pub type P1e = crate::Reg<p1e::P1eSpec>;
#[doc = "HYPERRAM_BIST.P1E, 32 bits at +0x78"]
pub mod p1e;
#[doc = "P1F (rw) register accessor: HYPERRAM_BIST.P1F, 32 bits at +0x7c\n\nYou can [`read`](crate::Reg::read) this register and get [`p1f::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`p1f::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@p1f`] module"]
#[doc(alias = "P1F")]
pub type P1f = crate::Reg<p1f::P1fSpec>;
#[doc = "HYPERRAM_BIST.P1F, 32 bits at +0x7c"]
pub mod p1f;
#[doc = "Q00 (r) register accessor: HYPERRAM_BIST.Q00, 32 bits at +0x100\n\nYou can [`read`](crate::Reg::read) this register and get [`q00::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q00`] module"]
pub type Q00 = crate::Reg<q00::Q00Spec>;
#[doc = "HYPERRAM_BIST.Q00, 32 bits at +0x100"]
pub mod q00;
#[doc = "Q01 (r) register accessor: HYPERRAM_BIST.Q01, 32 bits at +0x104\n\nYou can [`read`](crate::Reg::read) this register and get [`q01::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q01`] module"]
pub type Q01 = crate::Reg<q01::Q01Spec>;
#[doc = "HYPERRAM_BIST.Q01, 32 bits at +0x104"]
pub mod q01;
#[doc = "Q02 (r) register accessor: HYPERRAM_BIST.Q02, 32 bits at +0x108\n\nYou can [`read`](crate::Reg::read) this register and get [`q02::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q02`] module"]
pub type Q02 = crate::Reg<q02::Q02Spec>;
#[doc = "HYPERRAM_BIST.Q02, 32 bits at +0x108"]
pub mod q02;
#[doc = "Q03 (r) register accessor: HYPERRAM_BIST.Q03, 32 bits at +0x10c\n\nYou can [`read`](crate::Reg::read) this register and get [`q03::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q03`] module"]
pub type Q03 = crate::Reg<q03::Q03Spec>;
#[doc = "HYPERRAM_BIST.Q03, 32 bits at +0x10c"]
pub mod q03;
#[doc = "Q04 (r) register accessor: HYPERRAM_BIST.Q04, 32 bits at +0x110\n\nYou can [`read`](crate::Reg::read) this register and get [`q04::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q04`] module"]
pub type Q04 = crate::Reg<q04::Q04Spec>;
#[doc = "HYPERRAM_BIST.Q04, 32 bits at +0x110"]
pub mod q04;
#[doc = "Q05 (r) register accessor: HYPERRAM_BIST.Q05, 32 bits at +0x114\n\nYou can [`read`](crate::Reg::read) this register and get [`q05::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q05`] module"]
pub type Q05 = crate::Reg<q05::Q05Spec>;
#[doc = "HYPERRAM_BIST.Q05, 32 bits at +0x114"]
pub mod q05;
#[doc = "Q06 (r) register accessor: HYPERRAM_BIST.Q06, 32 bits at +0x118\n\nYou can [`read`](crate::Reg::read) this register and get [`q06::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q06`] module"]
pub type Q06 = crate::Reg<q06::Q06Spec>;
#[doc = "HYPERRAM_BIST.Q06, 32 bits at +0x118"]
pub mod q06;
#[doc = "Q07 (r) register accessor: HYPERRAM_BIST.Q07, 32 bits at +0x11c\n\nYou can [`read`](crate::Reg::read) this register and get [`q07::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q07`] module"]
pub type Q07 = crate::Reg<q07::Q07Spec>;
#[doc = "HYPERRAM_BIST.Q07, 32 bits at +0x11c"]
pub mod q07;
#[doc = "Q08 (r) register accessor: HYPERRAM_BIST.Q08, 32 bits at +0x120\n\nYou can [`read`](crate::Reg::read) this register and get [`q08::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q08`] module"]
pub type Q08 = crate::Reg<q08::Q08Spec>;
#[doc = "HYPERRAM_BIST.Q08, 32 bits at +0x120"]
pub mod q08;
#[doc = "Q09 (r) register accessor: HYPERRAM_BIST.Q09, 32 bits at +0x124\n\nYou can [`read`](crate::Reg::read) this register and get [`q09::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q09`] module"]
pub type Q09 = crate::Reg<q09::Q09Spec>;
#[doc = "HYPERRAM_BIST.Q09, 32 bits at +0x124"]
pub mod q09;
#[doc = "Q0A (r) register accessor: HYPERRAM_BIST.Q0A, 32 bits at +0x128\n\nYou can [`read`](crate::Reg::read) this register and get [`q0a::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q0a`] module"]
#[doc(alias = "Q0A")]
pub type Q0a = crate::Reg<q0a::Q0aSpec>;
#[doc = "HYPERRAM_BIST.Q0A, 32 bits at +0x128"]
pub mod q0a;
#[doc = "Q0B (r) register accessor: HYPERRAM_BIST.Q0B, 32 bits at +0x12c\n\nYou can [`read`](crate::Reg::read) this register and get [`q0b::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q0b`] module"]
#[doc(alias = "Q0B")]
pub type Q0b = crate::Reg<q0b::Q0bSpec>;
#[doc = "HYPERRAM_BIST.Q0B, 32 bits at +0x12c"]
pub mod q0b;
#[doc = "Q0C (r) register accessor: HYPERRAM_BIST.Q0C, 32 bits at +0x130\n\nYou can [`read`](crate::Reg::read) this register and get [`q0c::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q0c`] module"]
#[doc(alias = "Q0C")]
pub type Q0c = crate::Reg<q0c::Q0cSpec>;
#[doc = "HYPERRAM_BIST.Q0C, 32 bits at +0x130"]
pub mod q0c;
#[doc = "Q0D (r) register accessor: HYPERRAM_BIST.Q0D, 32 bits at +0x134\n\nYou can [`read`](crate::Reg::read) this register and get [`q0d::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q0d`] module"]
#[doc(alias = "Q0D")]
pub type Q0d = crate::Reg<q0d::Q0dSpec>;
#[doc = "HYPERRAM_BIST.Q0D, 32 bits at +0x134"]
pub mod q0d;
#[doc = "Q0E (r) register accessor: HYPERRAM_BIST.Q0E, 32 bits at +0x138\n\nYou can [`read`](crate::Reg::read) this register and get [`q0e::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q0e`] module"]
#[doc(alias = "Q0E")]
pub type Q0e = crate::Reg<q0e::Q0eSpec>;
#[doc = "HYPERRAM_BIST.Q0E, 32 bits at +0x138"]
pub mod q0e;
#[doc = "Q0F (r) register accessor: HYPERRAM_BIST.Q0F, 32 bits at +0x13c\n\nYou can [`read`](crate::Reg::read) this register and get [`q0f::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q0f`] module"]
#[doc(alias = "Q0F")]
pub type Q0f = crate::Reg<q0f::Q0fSpec>;
#[doc = "HYPERRAM_BIST.Q0F, 32 bits at +0x13c"]
pub mod q0f;
#[doc = "Q10 (r) register accessor: HYPERRAM_BIST.Q10, 32 bits at +0x140\n\nYou can [`read`](crate::Reg::read) this register and get [`q10::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q10`] module"]
pub type Q10 = crate::Reg<q10::Q10Spec>;
#[doc = "HYPERRAM_BIST.Q10, 32 bits at +0x140"]
pub mod q10;
#[doc = "Q11 (r) register accessor: HYPERRAM_BIST.Q11, 32 bits at +0x144\n\nYou can [`read`](crate::Reg::read) this register and get [`q11::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q11`] module"]
pub type Q11 = crate::Reg<q11::Q11Spec>;
#[doc = "HYPERRAM_BIST.Q11, 32 bits at +0x144"]
pub mod q11;
#[doc = "Q12 (r) register accessor: HYPERRAM_BIST.Q12, 32 bits at +0x148\n\nYou can [`read`](crate::Reg::read) this register and get [`q12::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q12`] module"]
pub type Q12 = crate::Reg<q12::Q12Spec>;
#[doc = "HYPERRAM_BIST.Q12, 32 bits at +0x148"]
pub mod q12;
#[doc = "Q13 (r) register accessor: HYPERRAM_BIST.Q13, 32 bits at +0x14c\n\nYou can [`read`](crate::Reg::read) this register and get [`q13::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q13`] module"]
pub type Q13 = crate::Reg<q13::Q13Spec>;
#[doc = "HYPERRAM_BIST.Q13, 32 bits at +0x14c"]
pub mod q13;
#[doc = "Q14 (r) register accessor: HYPERRAM_BIST.Q14, 32 bits at +0x150\n\nYou can [`read`](crate::Reg::read) this register and get [`q14::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q14`] module"]
pub type Q14 = crate::Reg<q14::Q14Spec>;
#[doc = "HYPERRAM_BIST.Q14, 32 bits at +0x150"]
pub mod q14;
#[doc = "Q15 (r) register accessor: HYPERRAM_BIST.Q15, 32 bits at +0x154\n\nYou can [`read`](crate::Reg::read) this register and get [`q15::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q15`] module"]
pub type Q15 = crate::Reg<q15::Q15Spec>;
#[doc = "HYPERRAM_BIST.Q15, 32 bits at +0x154"]
pub mod q15;
#[doc = "Q16 (r) register accessor: HYPERRAM_BIST.Q16, 32 bits at +0x158\n\nYou can [`read`](crate::Reg::read) this register and get [`q16::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q16`] module"]
pub type Q16 = crate::Reg<q16::Q16Spec>;
#[doc = "HYPERRAM_BIST.Q16, 32 bits at +0x158"]
pub mod q16;
#[doc = "Q17 (r) register accessor: HYPERRAM_BIST.Q17, 32 bits at +0x15c\n\nYou can [`read`](crate::Reg::read) this register and get [`q17::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q17`] module"]
pub type Q17 = crate::Reg<q17::Q17Spec>;
#[doc = "HYPERRAM_BIST.Q17, 32 bits at +0x15c"]
pub mod q17;
#[doc = "Q18 (r) register accessor: HYPERRAM_BIST.Q18, 32 bits at +0x160\n\nYou can [`read`](crate::Reg::read) this register and get [`q18::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q18`] module"]
pub type Q18 = crate::Reg<q18::Q18Spec>;
#[doc = "HYPERRAM_BIST.Q18, 32 bits at +0x160"]
pub mod q18;
#[doc = "Q19 (r) register accessor: HYPERRAM_BIST.Q19, 32 bits at +0x164\n\nYou can [`read`](crate::Reg::read) this register and get [`q19::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q19`] module"]
pub type Q19 = crate::Reg<q19::Q19Spec>;
#[doc = "HYPERRAM_BIST.Q19, 32 bits at +0x164"]
pub mod q19;
#[doc = "Q1A (r) register accessor: HYPERRAM_BIST.Q1A, 32 bits at +0x168\n\nYou can [`read`](crate::Reg::read) this register and get [`q1a::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q1a`] module"]
#[doc(alias = "Q1A")]
pub type Q1a = crate::Reg<q1a::Q1aSpec>;
#[doc = "HYPERRAM_BIST.Q1A, 32 bits at +0x168"]
pub mod q1a;
#[doc = "Q1B (r) register accessor: HYPERRAM_BIST.Q1B, 32 bits at +0x16c\n\nYou can [`read`](crate::Reg::read) this register and get [`q1b::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q1b`] module"]
#[doc(alias = "Q1B")]
pub type Q1b = crate::Reg<q1b::Q1bSpec>;
#[doc = "HYPERRAM_BIST.Q1B, 32 bits at +0x16c"]
pub mod q1b;
#[doc = "Q1C (r) register accessor: HYPERRAM_BIST.Q1C, 32 bits at +0x170\n\nYou can [`read`](crate::Reg::read) this register and get [`q1c::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q1c`] module"]
#[doc(alias = "Q1C")]
pub type Q1c = crate::Reg<q1c::Q1cSpec>;
#[doc = "HYPERRAM_BIST.Q1C, 32 bits at +0x170"]
pub mod q1c;
#[doc = "Q1D (r) register accessor: HYPERRAM_BIST.Q1D, 32 bits at +0x174\n\nYou can [`read`](crate::Reg::read) this register and get [`q1d::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q1d`] module"]
#[doc(alias = "Q1D")]
pub type Q1d = crate::Reg<q1d::Q1dSpec>;
#[doc = "HYPERRAM_BIST.Q1D, 32 bits at +0x174"]
pub mod q1d;
#[doc = "Q1E (r) register accessor: HYPERRAM_BIST.Q1E, 32 bits at +0x178\n\nYou can [`read`](crate::Reg::read) this register and get [`q1e::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q1e`] module"]
#[doc(alias = "Q1E")]
pub type Q1e = crate::Reg<q1e::Q1eSpec>;
#[doc = "HYPERRAM_BIST.Q1E, 32 bits at +0x178"]
pub mod q1e;
#[doc = "Q1F (r) register accessor: HYPERRAM_BIST.Q1F, 32 bits at +0x17c\n\nYou can [`read`](crate::Reg::read) this register and get [`q1f::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@q1f`] module"]
#[doc(alias = "Q1F")]
pub type Q1f = crate::Reg<q1f::Q1fSpec>;
#[doc = "HYPERRAM_BIST.Q1F, 32 bits at +0x17c"]
pub mod q1f;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const P00: usize = 0x00;
    pub const P01: usize = 0x04;
    pub const P02: usize = 0x08;
    pub const P03: usize = 0x0c;
    pub const P04: usize = 0x10;
    pub const P05: usize = 0x14;
    pub const P06: usize = 0x18;
    pub const P07: usize = 0x1c;
    pub const P08: usize = 0x20;
    pub const P09: usize = 0x24;
    pub const P0A: usize = 0x28;
    pub const P0B: usize = 0x2c;
    pub const P0C: usize = 0x30;
    pub const P0D: usize = 0x34;
    pub const P0E: usize = 0x38;
    pub const P0F: usize = 0x3c;
    pub const P10: usize = 0x40;
    pub const P11: usize = 0x44;
    pub const P12: usize = 0x48;
    pub const P13: usize = 0x4c;
    pub const P14: usize = 0x50;
    pub const P15: usize = 0x54;
    pub const P16: usize = 0x58;
    pub const P17: usize = 0x5c;
    pub const P18: usize = 0x60;
    pub const P19: usize = 0x64;
    pub const P1A: usize = 0x68;
    pub const P1B: usize = 0x6c;
    pub const P1C: usize = 0x70;
    pub const P1D: usize = 0x74;
    pub const P1E: usize = 0x78;
    pub const P1F: usize = 0x7c;
    pub const Q00: usize = 0x100;
    pub const Q01: usize = 0x104;
    pub const Q02: usize = 0x108;
    pub const Q03: usize = 0x10c;
    pub const Q04: usize = 0x110;
    pub const Q05: usize = 0x114;
    pub const Q06: usize = 0x118;
    pub const Q07: usize = 0x11c;
    pub const Q08: usize = 0x120;
    pub const Q09: usize = 0x124;
    pub const Q0A: usize = 0x128;
    pub const Q0B: usize = 0x12c;
    pub const Q0C: usize = 0x130;
    pub const Q0D: usize = 0x134;
    pub const Q0E: usize = 0x138;
    pub const Q0F: usize = 0x13c;
    pub const Q10: usize = 0x140;
    pub const Q11: usize = 0x144;
    pub const Q12: usize = 0x148;
    pub const Q13: usize = 0x14c;
    pub const Q14: usize = 0x150;
    pub const Q15: usize = 0x154;
    pub const Q16: usize = 0x158;
    pub const Q17: usize = 0x15c;
    pub const Q18: usize = 0x160;
    pub const Q19: usize = 0x164;
    pub const Q1A: usize = 0x168;
    pub const Q1B: usize = 0x16c;
    pub const Q1C: usize = 0x170;
    pub const Q1D: usize = 0x174;
    pub const Q1E: usize = 0x178;
    pub const Q1F: usize = 0x17c;
}
