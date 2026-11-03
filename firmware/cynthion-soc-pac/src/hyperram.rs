#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    id: Id,
    ck_khz: CkKhz,
    status: Status,
    _reserved3: [u8; 0x02],
    cmd: Cmd,
    ctrl: Ctrl,
    events: Events,
    irq_en: IrqEn,
    errors: Errors,
    _reserved8: [u8; 0x02],
    hr_addr: HrAddr,
    win_addr: WinAddr,
    _reserved10: [u8; 0x02],
    len: Len,
    fill_value: FillValue,
    mode: Mode,
    reg_sel: RegSel,
    latency: Latency,
    delay: Delay,
    seed: Seed,
    xor: Xor,
    burst: Burst,
    reg_wdata: RegWdata,
    reg_rdata: RegRdata,
    _reserved21: [u8; 0x02],
    cal: Cal,
    cal_map0: CalMap0,
    cal_map1: CalMap1,
    cal_map2: CalMap2,
    cal_map3: CalMap3,
    cycles: Cycles,
    words: Words,
    xacts: Xacts,
    checked: Checked,
    errs: Errs,
    bad_index: BadIndex,
    bad_got_want: BadGotWant,
    bad_bits: BadBits,
    state: State,
    missing: Missing,
    ck_count: CkCount,
    stream_base: StreamBase,
    stream_mask: StreamMask,
    stream_ctrl: StreamCtrl,
    _reserved40: [u8; 0x03],
    stream_level: StreamLevel,
}
impl RegisterBlock {
    #[doc = "0x00 - HYPERRAM.ID, 32 bits at +0x00"]
    #[inline(always)]
    pub const fn id(&self) -> &Id {
        &self.id
    }
    #[doc = "0x04 - HYPERRAM.CK_KHZ, 32 bits at +0x04"]
    #[inline(always)]
    pub const fn ck_khz(&self) -> &CkKhz {
        &self.ck_khz
    }
    #[doc = "0x08 - HYPERRAM.STATUS, 16 bits at +0x08"]
    #[inline(always)]
    pub const fn status(&self) -> &Status {
        &self.status
    }
    #[doc = "0x0c - HYPERRAM.CMD, 4 bits at +0x0c"]
    #[inline(always)]
    pub const fn cmd(&self) -> &Cmd {
        &self.cmd
    }
    #[doc = "0x0d - HYPERRAM.CTRL, 1 bits at +0x0d"]
    #[inline(always)]
    pub const fn ctrl(&self) -> &Ctrl {
        &self.ctrl
    }
    #[doc = "0x0e - HYPERRAM.EVENTS, 2 bits at +0x0e"]
    #[inline(always)]
    pub const fn events(&self) -> &Events {
        &self.events
    }
    #[doc = "0x0f - HYPERRAM.IRQ_EN, 2 bits at +0x0f"]
    #[inline(always)]
    pub const fn irq_en(&self) -> &IrqEn {
        &self.irq_en
    }
    #[doc = "0x10 - HYPERRAM.ERRORS, 13 bits at +0x10"]
    #[inline(always)]
    pub const fn errors(&self) -> &Errors {
        &self.errors
    }
    #[doc = "0x14 - HYPERRAM.HR_ADDR, 23 bits at +0x14"]
    #[inline(always)]
    pub const fn hr_addr(&self) -> &HrAddr {
        &self.hr_addr
    }
    #[doc = "0x18 - HYPERRAM.WIN_ADDR, 12 bits at +0x18"]
    #[inline(always)]
    pub const fn win_addr(&self) -> &WinAddr {
        &self.win_addr
    }
    #[doc = "0x1c - HYPERRAM.LEN, 24 bits at +0x1c"]
    #[inline(always)]
    pub const fn len(&self) -> &Len {
        &self.len
    }
    #[doc = "0x20 - HYPERRAM.FILL_VALUE, 32 bits at +0x20"]
    #[inline(always)]
    pub const fn fill_value(&self) -> &FillValue {
        &self.fill_value
    }
    #[doc = "0x24 - HYPERRAM.MODE, 5 bits at +0x24"]
    #[inline(always)]
    pub const fn mode(&self) -> &Mode {
        &self.mode
    }
    #[doc = "0x25 - HYPERRAM.REG_SEL, 2 bits at +0x25"]
    #[inline(always)]
    pub const fn reg_sel(&self) -> &RegSel {
        &self.reg_sel
    }
    #[doc = "0x26 - HYPERRAM.LATENCY, 5 bits at +0x26"]
    #[inline(always)]
    pub const fn latency(&self) -> &Latency {
        &self.latency
    }
    #[doc = "0x27 - HYPERRAM.DELAY, 7 bits at +0x27"]
    #[inline(always)]
    pub const fn delay(&self) -> &Delay {
        &self.delay
    }
    #[doc = "0x28 - HYPERRAM.SEED, 16 bits at +0x28"]
    #[inline(always)]
    pub const fn seed(&self) -> &Seed {
        &self.seed
    }
    #[doc = "0x2a - HYPERRAM.XOR, 16 bits at +0x2a"]
    #[inline(always)]
    pub const fn xor(&self) -> &Xor {
        &self.xor
    }
    #[doc = "0x2c - HYPERRAM.BURST, 16 bits at +0x2c"]
    #[inline(always)]
    pub const fn burst(&self) -> &Burst {
        &self.burst
    }
    #[doc = "0x2e - HYPERRAM.REG_WDATA, 16 bits at +0x2e"]
    #[inline(always)]
    pub const fn reg_wdata(&self) -> &RegWdata {
        &self.reg_wdata
    }
    #[doc = "0x30 - HYPERRAM.REG_RDATA, 16 bits at +0x30"]
    #[inline(always)]
    pub const fn reg_rdata(&self) -> &RegRdata {
        &self.reg_rdata
    }
    #[doc = "0x34 - HYPERRAM.CAL, 17 bits at +0x34"]
    #[inline(always)]
    pub const fn cal(&self) -> &Cal {
        &self.cal
    }
    #[doc = "0x38 - HYPERRAM.CAL_MAP0, 32 bits at +0x38"]
    #[inline(always)]
    pub const fn cal_map0(&self) -> &CalMap0 {
        &self.cal_map0
    }
    #[doc = "0x3c - HYPERRAM.CAL_MAP1, 32 bits at +0x3c"]
    #[inline(always)]
    pub const fn cal_map1(&self) -> &CalMap1 {
        &self.cal_map1
    }
    #[doc = "0x40 - HYPERRAM.CAL_MAP2, 32 bits at +0x40"]
    #[inline(always)]
    pub const fn cal_map2(&self) -> &CalMap2 {
        &self.cal_map2
    }
    #[doc = "0x44 - HYPERRAM.CAL_MAP3, 32 bits at +0x44"]
    #[inline(always)]
    pub const fn cal_map3(&self) -> &CalMap3 {
        &self.cal_map3
    }
    #[doc = "0x48 - HYPERRAM.CYCLES, 32 bits at +0x48"]
    #[inline(always)]
    pub const fn cycles(&self) -> &Cycles {
        &self.cycles
    }
    #[doc = "0x4c - HYPERRAM.WORDS, 32 bits at +0x4c"]
    #[inline(always)]
    pub const fn words(&self) -> &Words {
        &self.words
    }
    #[doc = "0x50 - HYPERRAM.XACTS, 32 bits at +0x50"]
    #[inline(always)]
    pub const fn xacts(&self) -> &Xacts {
        &self.xacts
    }
    #[doc = "0x54 - HYPERRAM.CHECKED, 32 bits at +0x54"]
    #[inline(always)]
    pub const fn checked(&self) -> &Checked {
        &self.checked
    }
    #[doc = "0x58 - HYPERRAM.ERRS, 32 bits at +0x58"]
    #[inline(always)]
    pub const fn errs(&self) -> &Errs {
        &self.errs
    }
    #[doc = "0x5c - HYPERRAM.BAD_INDEX, 32 bits at +0x5c"]
    #[inline(always)]
    pub const fn bad_index(&self) -> &BadIndex {
        &self.bad_index
    }
    #[doc = "0x60 - HYPERRAM.BAD_GOT_WANT, 32 bits at +0x60"]
    #[inline(always)]
    pub const fn bad_got_want(&self) -> &BadGotWant {
        &self.bad_got_want
    }
    #[doc = "0x64 - HYPERRAM.BAD_BITS, 16 bits at +0x64"]
    #[inline(always)]
    pub const fn bad_bits(&self) -> &BadBits {
        &self.bad_bits
    }
    #[doc = "0x66 - HYPERRAM.STATE, 16 bits at +0x66"]
    #[inline(always)]
    pub const fn state(&self) -> &State {
        &self.state
    }
    #[doc = "0x68 - HYPERRAM.MISSING, 32 bits at +0x68"]
    #[inline(always)]
    pub const fn missing(&self) -> &Missing {
        &self.missing
    }
    #[doc = "0x6c - HYPERRAM.CK_COUNT, 32 bits at +0x6c"]
    #[inline(always)]
    pub const fn ck_count(&self) -> &CkCount {
        &self.ck_count
    }
    #[doc = "0x70 - HYPERRAM.STREAM_BASE, 22 bits at +0x70"]
    #[inline(always)]
    pub const fn stream_base(&self) -> &StreamBase {
        &self.stream_base
    }
    #[doc = "0x74 - HYPERRAM.STREAM_MASK, 22 bits at +0x74"]
    #[inline(always)]
    pub const fn stream_mask(&self) -> &StreamMask {
        &self.stream_mask
    }
    #[doc = "0x78 - HYPERRAM.STREAM_CTRL, 1 bits at +0x78"]
    #[inline(always)]
    pub const fn stream_ctrl(&self) -> &StreamCtrl {
        &self.stream_ctrl
    }
    #[doc = "0x7c - HYPERRAM.STREAM_LEVEL, 23 bits at +0x7c"]
    #[inline(always)]
    pub const fn stream_level(&self) -> &StreamLevel {
        &self.stream_level
    }
}
#[doc = "ID (r) register accessor: HYPERRAM.ID, 32 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`id::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@id`] module"]
#[doc(alias = "ID")]
pub type Id = crate::Reg<id::IdSpec>;
#[doc = "HYPERRAM.ID, 32 bits at +0x00"]
pub mod id;
#[doc = "CK_KHZ (r) register accessor: HYPERRAM.CK_KHZ, 32 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`ck_khz::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ck_khz`] module"]
#[doc(alias = "CK_KHZ")]
pub type CkKhz = crate::Reg<ck_khz::CkKhzSpec>;
#[doc = "HYPERRAM.CK_KHZ, 32 bits at +0x04"]
pub mod ck_khz;
#[doc = "STATUS (r) register accessor: HYPERRAM.STATUS, 16 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@status`] module"]
#[doc(alias = "STATUS")]
pub type Status = crate::Reg<status::StatusSpec>;
#[doc = "HYPERRAM.STATUS, 16 bits at +0x08"]
pub mod status;
#[doc = "CMD (w) register accessor: HYPERRAM.CMD, 4 bits at +0x0c\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`cmd::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cmd`] module"]
#[doc(alias = "CMD")]
pub type Cmd = crate::Reg<cmd::CmdSpec>;
#[doc = "HYPERRAM.CMD, 4 bits at +0x0c"]
pub mod cmd;
#[doc = "CTRL (w) register accessor: HYPERRAM.CTRL, 1 bits at +0x0d\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ctrl::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ctrl`] module"]
#[doc(alias = "CTRL")]
pub type Ctrl = crate::Reg<ctrl::CtrlSpec>;
#[doc = "HYPERRAM.CTRL, 1 bits at +0x0d"]
pub mod ctrl;
#[doc = "EVENTS (rw) register accessor: HYPERRAM.EVENTS, 2 bits at +0x0e\n\nYou can [`read`](crate::Reg::read) this register and get [`events::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`events::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@events`] module"]
#[doc(alias = "EVENTS")]
pub type Events = crate::Reg<events::EventsSpec>;
#[doc = "HYPERRAM.EVENTS, 2 bits at +0x0e"]
pub mod events;
#[doc = "IRQ_EN (rw) register accessor: HYPERRAM.IRQ_EN, 2 bits at +0x0f\n\nYou can [`read`](crate::Reg::read) this register and get [`irq_en::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`irq_en::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@irq_en`] module"]
#[doc(alias = "IRQ_EN")]
pub type IrqEn = crate::Reg<irq_en::IrqEnSpec>;
#[doc = "HYPERRAM.IRQ_EN, 2 bits at +0x0f"]
pub mod irq_en;
#[doc = "ERRORS (rw) register accessor: HYPERRAM.ERRORS, 13 bits at +0x10\n\nYou can [`read`](crate::Reg::read) this register and get [`errors::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`errors::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@errors`] module"]
#[doc(alias = "ERRORS")]
pub type Errors = crate::Reg<errors::ErrorsSpec>;
#[doc = "HYPERRAM.ERRORS, 13 bits at +0x10"]
pub mod errors;
#[doc = "HR_ADDR (rw) register accessor: HYPERRAM.HR_ADDR, 23 bits at +0x14\n\nYou can [`read`](crate::Reg::read) this register and get [`hr_addr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`hr_addr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@hr_addr`] module"]
#[doc(alias = "HR_ADDR")]
pub type HrAddr = crate::Reg<hr_addr::HrAddrSpec>;
#[doc = "HYPERRAM.HR_ADDR, 23 bits at +0x14"]
pub mod hr_addr;
#[doc = "WIN_ADDR (rw) register accessor: HYPERRAM.WIN_ADDR, 12 bits at +0x18\n\nYou can [`read`](crate::Reg::read) this register and get [`win_addr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`win_addr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@win_addr`] module"]
#[doc(alias = "WIN_ADDR")]
pub type WinAddr = crate::Reg<win_addr::WinAddrSpec>;
#[doc = "HYPERRAM.WIN_ADDR, 12 bits at +0x18"]
pub mod win_addr;
#[doc = "LEN (rw) register accessor: HYPERRAM.LEN, 24 bits at +0x1c\n\nYou can [`read`](crate::Reg::read) this register and get [`len::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`len::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@len`] module"]
#[doc(alias = "LEN")]
pub type Len = crate::Reg<len::LenSpec>;
#[doc = "HYPERRAM.LEN, 24 bits at +0x1c"]
pub mod len;
#[doc = "FILL_VALUE (rw) register accessor: HYPERRAM.FILL_VALUE, 32 bits at +0x20\n\nYou can [`read`](crate::Reg::read) this register and get [`fill_value::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`fill_value::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@fill_value`] module"]
#[doc(alias = "FILL_VALUE")]
pub type FillValue = crate::Reg<fill_value::FillValueSpec>;
#[doc = "HYPERRAM.FILL_VALUE, 32 bits at +0x20"]
pub mod fill_value;
#[doc = "MODE (rw) register accessor: HYPERRAM.MODE, 5 bits at +0x24\n\nYou can [`read`](crate::Reg::read) this register and get [`mode::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`mode::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@mode`] module"]
#[doc(alias = "MODE")]
pub type Mode = crate::Reg<mode::ModeSpec>;
#[doc = "HYPERRAM.MODE, 5 bits at +0x24"]
pub mod mode;
#[doc = "REG_SEL (rw) register accessor: HYPERRAM.REG_SEL, 2 bits at +0x25\n\nYou can [`read`](crate::Reg::read) this register and get [`reg_sel::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`reg_sel::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@reg_sel`] module"]
#[doc(alias = "REG_SEL")]
pub type RegSel = crate::Reg<reg_sel::RegSelSpec>;
#[doc = "HYPERRAM.REG_SEL, 2 bits at +0x25"]
pub mod reg_sel;
#[doc = "LATENCY (rw) register accessor: HYPERRAM.LATENCY, 5 bits at +0x26\n\nYou can [`read`](crate::Reg::read) this register and get [`latency::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`latency::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@latency`] module"]
#[doc(alias = "LATENCY")]
pub type Latency = crate::Reg<latency::LatencySpec>;
#[doc = "HYPERRAM.LATENCY, 5 bits at +0x26"]
pub mod latency;
#[doc = "DELAY (rw) register accessor: HYPERRAM.DELAY, 7 bits at +0x27\n\nYou can [`read`](crate::Reg::read) this register and get [`delay::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`delay::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@delay`] module"]
#[doc(alias = "DELAY")]
pub type Delay = crate::Reg<delay::DelaySpec>;
#[doc = "HYPERRAM.DELAY, 7 bits at +0x27"]
pub mod delay;
#[doc = "SEED (rw) register accessor: HYPERRAM.SEED, 16 bits at +0x28\n\nYou can [`read`](crate::Reg::read) this register and get [`seed::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`seed::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@seed`] module"]
#[doc(alias = "SEED")]
pub type Seed = crate::Reg<seed::SeedSpec>;
#[doc = "HYPERRAM.SEED, 16 bits at +0x28"]
pub mod seed;
#[doc = "XOR (rw) register accessor: HYPERRAM.XOR, 16 bits at +0x2a\n\nYou can [`read`](crate::Reg::read) this register and get [`xor::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`xor::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@xor`] module"]
#[doc(alias = "XOR")]
pub type Xor = crate::Reg<xor::XorSpec>;
#[doc = "HYPERRAM.XOR, 16 bits at +0x2a"]
pub mod xor;
#[doc = "BURST (rw) register accessor: HYPERRAM.BURST, 16 bits at +0x2c\n\nYou can [`read`](crate::Reg::read) this register and get [`burst::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`burst::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@burst`] module"]
#[doc(alias = "BURST")]
pub type Burst = crate::Reg<burst::BurstSpec>;
#[doc = "HYPERRAM.BURST, 16 bits at +0x2c"]
pub mod burst;
#[doc = "REG_WDATA (rw) register accessor: HYPERRAM.REG_WDATA, 16 bits at +0x2e\n\nYou can [`read`](crate::Reg::read) this register and get [`reg_wdata::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`reg_wdata::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@reg_wdata`] module"]
#[doc(alias = "REG_WDATA")]
pub type RegWdata = crate::Reg<reg_wdata::RegWdataSpec>;
#[doc = "HYPERRAM.REG_WDATA, 16 bits at +0x2e"]
pub mod reg_wdata;
#[doc = "REG_RDATA (r) register accessor: HYPERRAM.REG_RDATA, 16 bits at +0x30\n\nYou can [`read`](crate::Reg::read) this register and get [`reg_rdata::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@reg_rdata`] module"]
#[doc(alias = "REG_RDATA")]
pub type RegRdata = crate::Reg<reg_rdata::RegRdataSpec>;
#[doc = "HYPERRAM.REG_RDATA, 16 bits at +0x30"]
pub mod reg_rdata;
#[doc = "CAL (r) register accessor: HYPERRAM.CAL, 17 bits at +0x34\n\nYou can [`read`](crate::Reg::read) this register and get [`cal::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cal`] module"]
#[doc(alias = "CAL")]
pub type Cal = crate::Reg<cal::CalSpec>;
#[doc = "HYPERRAM.CAL, 17 bits at +0x34"]
pub mod cal;
#[doc = "CAL_MAP0 (r) register accessor: HYPERRAM.CAL_MAP0, 32 bits at +0x38\n\nYou can [`read`](crate::Reg::read) this register and get [`cal_map0::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cal_map0`] module"]
#[doc(alias = "CAL_MAP0")]
pub type CalMap0 = crate::Reg<cal_map0::CalMap0Spec>;
#[doc = "HYPERRAM.CAL_MAP0, 32 bits at +0x38"]
pub mod cal_map0;
#[doc = "CAL_MAP1 (r) register accessor: HYPERRAM.CAL_MAP1, 32 bits at +0x3c\n\nYou can [`read`](crate::Reg::read) this register and get [`cal_map1::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cal_map1`] module"]
#[doc(alias = "CAL_MAP1")]
pub type CalMap1 = crate::Reg<cal_map1::CalMap1Spec>;
#[doc = "HYPERRAM.CAL_MAP1, 32 bits at +0x3c"]
pub mod cal_map1;
#[doc = "CAL_MAP2 (r) register accessor: HYPERRAM.CAL_MAP2, 32 bits at +0x40\n\nYou can [`read`](crate::Reg::read) this register and get [`cal_map2::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cal_map2`] module"]
#[doc(alias = "CAL_MAP2")]
pub type CalMap2 = crate::Reg<cal_map2::CalMap2Spec>;
#[doc = "HYPERRAM.CAL_MAP2, 32 bits at +0x40"]
pub mod cal_map2;
#[doc = "CAL_MAP3 (r) register accessor: HYPERRAM.CAL_MAP3, 32 bits at +0x44\n\nYou can [`read`](crate::Reg::read) this register and get [`cal_map3::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cal_map3`] module"]
#[doc(alias = "CAL_MAP3")]
pub type CalMap3 = crate::Reg<cal_map3::CalMap3Spec>;
#[doc = "HYPERRAM.CAL_MAP3, 32 bits at +0x44"]
pub mod cal_map3;
#[doc = "CYCLES (r) register accessor: HYPERRAM.CYCLES, 32 bits at +0x48\n\nYou can [`read`](crate::Reg::read) this register and get [`cycles::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cycles`] module"]
#[doc(alias = "CYCLES")]
pub type Cycles = crate::Reg<cycles::CyclesSpec>;
#[doc = "HYPERRAM.CYCLES, 32 bits at +0x48"]
pub mod cycles;
#[doc = "WORDS (r) register accessor: HYPERRAM.WORDS, 32 bits at +0x4c\n\nYou can [`read`](crate::Reg::read) this register and get [`words::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@words`] module"]
#[doc(alias = "WORDS")]
pub type Words = crate::Reg<words::WordsSpec>;
#[doc = "HYPERRAM.WORDS, 32 bits at +0x4c"]
pub mod words;
#[doc = "XACTS (r) register accessor: HYPERRAM.XACTS, 32 bits at +0x50\n\nYou can [`read`](crate::Reg::read) this register and get [`xacts::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@xacts`] module"]
#[doc(alias = "XACTS")]
pub type Xacts = crate::Reg<xacts::XactsSpec>;
#[doc = "HYPERRAM.XACTS, 32 bits at +0x50"]
pub mod xacts;
#[doc = "CHECKED (r) register accessor: HYPERRAM.CHECKED, 32 bits at +0x54\n\nYou can [`read`](crate::Reg::read) this register and get [`checked::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@checked`] module"]
#[doc(alias = "CHECKED")]
pub type Checked = crate::Reg<checked::CheckedSpec>;
#[doc = "HYPERRAM.CHECKED, 32 bits at +0x54"]
pub mod checked;
#[doc = "ERRS (r) register accessor: HYPERRAM.ERRS, 32 bits at +0x58\n\nYou can [`read`](crate::Reg::read) this register and get [`errs::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@errs`] module"]
#[doc(alias = "ERRS")]
pub type Errs = crate::Reg<errs::ErrsSpec>;
#[doc = "HYPERRAM.ERRS, 32 bits at +0x58"]
pub mod errs;
#[doc = "BAD_INDEX (r) register accessor: HYPERRAM.BAD_INDEX, 32 bits at +0x5c\n\nYou can [`read`](crate::Reg::read) this register and get [`bad_index::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@bad_index`] module"]
#[doc(alias = "BAD_INDEX")]
pub type BadIndex = crate::Reg<bad_index::BadIndexSpec>;
#[doc = "HYPERRAM.BAD_INDEX, 32 bits at +0x5c"]
pub mod bad_index;
#[doc = "BAD_GOT_WANT (r) register accessor: HYPERRAM.BAD_GOT_WANT, 32 bits at +0x60\n\nYou can [`read`](crate::Reg::read) this register and get [`bad_got_want::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@bad_got_want`] module"]
#[doc(alias = "BAD_GOT_WANT")]
pub type BadGotWant = crate::Reg<bad_got_want::BadGotWantSpec>;
#[doc = "HYPERRAM.BAD_GOT_WANT, 32 bits at +0x60"]
pub mod bad_got_want;
#[doc = "BAD_BITS (r) register accessor: HYPERRAM.BAD_BITS, 16 bits at +0x64\n\nYou can [`read`](crate::Reg::read) this register and get [`bad_bits::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@bad_bits`] module"]
#[doc(alias = "BAD_BITS")]
pub type BadBits = crate::Reg<bad_bits::BadBitsSpec>;
#[doc = "HYPERRAM.BAD_BITS, 16 bits at +0x64"]
pub mod bad_bits;
#[doc = "STATE (r) register accessor: HYPERRAM.STATE, 16 bits at +0x66\n\nYou can [`read`](crate::Reg::read) this register and get [`state::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@state`] module"]
#[doc(alias = "STATE")]
pub type State = crate::Reg<state::StateSpec>;
#[doc = "HYPERRAM.STATE, 16 bits at +0x66"]
pub mod state;
#[doc = "MISSING (r) register accessor: HYPERRAM.MISSING, 32 bits at +0x68\n\nYou can [`read`](crate::Reg::read) this register and get [`missing::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@missing`] module"]
#[doc(alias = "MISSING")]
pub type Missing = crate::Reg<missing::MissingSpec>;
#[doc = "HYPERRAM.MISSING, 32 bits at +0x68"]
pub mod missing;
#[doc = "CK_COUNT (r) register accessor: HYPERRAM.CK_COUNT, 32 bits at +0x6c\n\nYou can [`read`](crate::Reg::read) this register and get [`ck_count::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ck_count`] module"]
#[doc(alias = "CK_COUNT")]
pub type CkCount = crate::Reg<ck_count::CkCountSpec>;
#[doc = "HYPERRAM.CK_COUNT, 32 bits at +0x6c"]
pub mod ck_count;
#[doc = "STREAM_BASE (rw) register accessor: HYPERRAM.STREAM_BASE, 22 bits at +0x70\n\nYou can [`read`](crate::Reg::read) this register and get [`stream_base::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`stream_base::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@stream_base`] module"]
#[doc(alias = "STREAM_BASE")]
pub type StreamBase = crate::Reg<stream_base::StreamBaseSpec>;
#[doc = "HYPERRAM.STREAM_BASE, 22 bits at +0x70"]
pub mod stream_base;
#[doc = "STREAM_MASK (rw) register accessor: HYPERRAM.STREAM_MASK, 22 bits at +0x74\n\nYou can [`read`](crate::Reg::read) this register and get [`stream_mask::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`stream_mask::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@stream_mask`] module"]
#[doc(alias = "STREAM_MASK")]
pub type StreamMask = crate::Reg<stream_mask::StreamMaskSpec>;
#[doc = "HYPERRAM.STREAM_MASK, 22 bits at +0x74"]
pub mod stream_mask;
#[doc = "STREAM_CTRL (rw) register accessor: HYPERRAM.STREAM_CTRL, 1 bits at +0x78\n\nYou can [`read`](crate::Reg::read) this register and get [`stream_ctrl::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`stream_ctrl::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@stream_ctrl`] module"]
#[doc(alias = "STREAM_CTRL")]
pub type StreamCtrl = crate::Reg<stream_ctrl::StreamCtrlSpec>;
#[doc = "HYPERRAM.STREAM_CTRL, 1 bits at +0x78"]
pub mod stream_ctrl;
#[doc = "STREAM_LEVEL (r) register accessor: HYPERRAM.STREAM_LEVEL, 23 bits at +0x7c\n\nYou can [`read`](crate::Reg::read) this register and get [`stream_level::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@stream_level`] module"]
#[doc(alias = "STREAM_LEVEL")]
pub type StreamLevel = crate::Reg<stream_level::StreamLevelSpec>;
#[doc = "HYPERRAM.STREAM_LEVEL, 23 bits at +0x7c"]
pub mod stream_level;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const ID: usize = 0x00;
    pub const CK_KHZ: usize = 0x04;
    pub const STATUS: usize = 0x08;
    pub const CMD: usize = 0x0c;
    pub const CTRL: usize = 0x0d;
    pub const EVENTS: usize = 0x0e;
    pub const IRQ_EN: usize = 0x0f;
    pub const ERRORS: usize = 0x10;
    pub const HR_ADDR: usize = 0x14;
    pub const WIN_ADDR: usize = 0x18;
    pub const LEN: usize = 0x1c;
    pub const FILL_VALUE: usize = 0x20;
    pub const MODE: usize = 0x24;
    pub const REG_SEL: usize = 0x25;
    pub const LATENCY: usize = 0x26;
    pub const DELAY: usize = 0x27;
    pub const SEED: usize = 0x28;
    pub const XOR: usize = 0x2a;
    pub const BURST: usize = 0x2c;
    pub const REG_WDATA: usize = 0x2e;
    pub const REG_RDATA: usize = 0x30;
    pub const CAL: usize = 0x34;
    pub const CAL_MAP0: usize = 0x38;
    pub const CAL_MAP1: usize = 0x3c;
    pub const CAL_MAP2: usize = 0x40;
    pub const CAL_MAP3: usize = 0x44;
    pub const CYCLES: usize = 0x48;
    pub const WORDS: usize = 0x4c;
    pub const XACTS: usize = 0x50;
    pub const CHECKED: usize = 0x54;
    pub const ERRS: usize = 0x58;
    pub const BAD_INDEX: usize = 0x5c;
    pub const BAD_GOT_WANT: usize = 0x60;
    pub const BAD_BITS: usize = 0x64;
    pub const STATE: usize = 0x66;
    pub const MISSING: usize = 0x68;
    pub const CK_COUNT: usize = 0x6c;
    pub const STREAM_BASE: usize = 0x70;
    pub const STREAM_MASK: usize = 0x74;
    pub const STREAM_CTRL: usize = 0x78;
    pub const STREAM_LEVEL: usize = 0x7c;
}
