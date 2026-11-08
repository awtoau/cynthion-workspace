#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    id: Id,
    _reserved1: [u8; 0x04],
    status: Status,
    _reserved2: [u8; 0x02],
    cmd: Cmd,
    ctrl: Ctrl,
    events: Events,
    irq_en: IrqEn,
    errors: Errors,
    _reserved7: [u8; 0x02],
    addr: Addr,
    buf_addr: BufAddr,
    len: Len,
    result: Result,
    write_lo: WriteLo,
    write_hi: WriteHi,
    _reserved13: [u8; 0x04],
    cal_addr: CalAddr,
    cal: Cal,
    _reserved15: [u8; 0x08],
    busy_cycles: BusyCycles,
    state: State,
    seq: Seq,
    ck_count: CkCount,
    _reserved19: [u8; 0x04],
    fill_last: FillLast,
    fill_max: FillMax,
}
impl RegisterBlock {
    #[doc = "0x00 - FLASH.ID, 32 bits at +0x00"]
    #[inline(always)]
    pub const fn id(&self) -> &Id {
        &self.id
    }
    #[doc = "0x08 - FLASH.STATUS, 16 bits at +0x08"]
    #[inline(always)]
    pub const fn status(&self) -> &Status {
        &self.status
    }
    #[doc = "0x0c - FLASH.CMD, 4 bits at +0x0c"]
    #[inline(always)]
    pub const fn cmd(&self) -> &Cmd {
        &self.cmd
    }
    #[doc = "0x0d - FLASH.CTRL, 1 bits at +0x0d"]
    #[inline(always)]
    pub const fn ctrl(&self) -> &Ctrl {
        &self.ctrl
    }
    #[doc = "0x0e - FLASH.EVENTS, 2 bits at +0x0e"]
    #[inline(always)]
    pub const fn events(&self) -> &Events {
        &self.events
    }
    #[doc = "0x0f - FLASH.IRQ_EN, 2 bits at +0x0f"]
    #[inline(always)]
    pub const fn irq_en(&self) -> &IrqEn {
        &self.irq_en
    }
    #[doc = "0x10 - FLASH.ERRORS, 10 bits at +0x10"]
    #[inline(always)]
    pub const fn errors(&self) -> &Errors {
        &self.errors
    }
    #[doc = "0x14 - FLASH.ADDR, 22 bits at +0x14"]
    #[inline(always)]
    pub const fn addr(&self) -> &Addr {
        &self.addr
    }
    #[doc = "0x18 - FLASH.BUF_ADDR, 12 bits at +0x18"]
    #[inline(always)]
    pub const fn buf_addr(&self) -> &BufAddr {
        &self.buf_addr
    }
    #[doc = "0x1a - FLASH.LEN, 13 bits at +0x1a"]
    #[inline(always)]
    pub const fn len(&self) -> &Len {
        &self.len
    }
    #[doc = "0x1c - FLASH.RESULT, 32 bits at +0x1c"]
    #[inline(always)]
    pub const fn result(&self) -> &Result {
        &self.result
    }
    #[doc = "0x20 - FLASH.WRITE_LO, 11 bits at +0x20"]
    #[inline(always)]
    pub const fn write_lo(&self) -> &WriteLo {
        &self.write_lo
    }
    #[doc = "0x22 - FLASH.WRITE_HI, 11 bits at +0x22"]
    #[inline(always)]
    pub const fn write_hi(&self) -> &WriteHi {
        &self.write_hi
    }
    #[doc = "0x28 - FLASH.CAL_ADDR, 22 bits at +0x28"]
    #[inline(always)]
    pub const fn cal_addr(&self) -> &CalAddr {
        &self.cal_addr
    }
    #[doc = "0x2c - FLASH.CAL, 32 bits at +0x2c"]
    #[inline(always)]
    pub const fn cal(&self) -> &Cal {
        &self.cal
    }
    #[doc = "0x38 - FLASH.BUSY_CYCLES, 29 bits at +0x38"]
    #[inline(always)]
    pub const fn busy_cycles(&self) -> &BusyCycles {
        &self.busy_cycles
    }
    #[doc = "0x3c - FLASH.STATE, 16 bits at +0x3c"]
    #[inline(always)]
    pub const fn state(&self) -> &State {
        &self.state
    }
    #[doc = "0x3e - FLASH.SEQ, 16 bits at +0x3e"]
    #[inline(always)]
    pub const fn seq(&self) -> &Seq {
        &self.seq
    }
    #[doc = "0x40 - FLASH.CK_COUNT, 22 bits at +0x40"]
    #[inline(always)]
    pub const fn ck_count(&self) -> &CkCount {
        &self.ck_count
    }
    #[doc = "0x48 - FLASH.FILL_LAST, 16 bits at +0x48"]
    #[inline(always)]
    pub const fn fill_last(&self) -> &FillLast {
        &self.fill_last
    }
    #[doc = "0x4a - FLASH.FILL_MAX, 16 bits at +0x4a"]
    #[inline(always)]
    pub const fn fill_max(&self) -> &FillMax {
        &self.fill_max
    }
}
#[doc = "ID (r) register accessor: FLASH.ID, 32 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`id::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@id`] module"]
#[doc(alias = "ID")]
pub type Id = crate::Reg<id::IdSpec>;
#[doc = "FLASH.ID, 32 bits at +0x00"]
pub mod id;
#[doc = "STATUS (r) register accessor: FLASH.STATUS, 16 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@status`] module"]
#[doc(alias = "STATUS")]
pub type Status = crate::Reg<status::StatusSpec>;
#[doc = "FLASH.STATUS, 16 bits at +0x08"]
pub mod status;
#[doc = "CMD (w) register accessor: FLASH.CMD, 4 bits at +0x0c\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`cmd::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cmd`] module"]
#[doc(alias = "CMD")]
pub type Cmd = crate::Reg<cmd::CmdSpec>;
#[doc = "FLASH.CMD, 4 bits at +0x0c"]
pub mod cmd;
#[doc = "CTRL (w) register accessor: FLASH.CTRL, 1 bits at +0x0d\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ctrl::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ctrl`] module"]
#[doc(alias = "CTRL")]
pub type Ctrl = crate::Reg<ctrl::CtrlSpec>;
#[doc = "FLASH.CTRL, 1 bits at +0x0d"]
pub mod ctrl;
#[doc = "EVENTS (rw) register accessor: FLASH.EVENTS, 2 bits at +0x0e\n\nYou can [`read`](crate::Reg::read) this register and get [`events::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`events::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@events`] module"]
#[doc(alias = "EVENTS")]
pub type Events = crate::Reg<events::EventsSpec>;
#[doc = "FLASH.EVENTS, 2 bits at +0x0e"]
pub mod events;
#[doc = "IRQ_EN (rw) register accessor: FLASH.IRQ_EN, 2 bits at +0x0f\n\nYou can [`read`](crate::Reg::read) this register and get [`irq_en::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`irq_en::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@irq_en`] module"]
#[doc(alias = "IRQ_EN")]
pub type IrqEn = crate::Reg<irq_en::IrqEnSpec>;
#[doc = "FLASH.IRQ_EN, 2 bits at +0x0f"]
pub mod irq_en;
#[doc = "ERRORS (rw) register accessor: FLASH.ERRORS, 10 bits at +0x10\n\nYou can [`read`](crate::Reg::read) this register and get [`errors::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`errors::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@errors`] module"]
#[doc(alias = "ERRORS")]
pub type Errors = crate::Reg<errors::ErrorsSpec>;
#[doc = "FLASH.ERRORS, 10 bits at +0x10"]
pub mod errors;
#[doc = "ADDR (rw) register accessor: FLASH.ADDR, 22 bits at +0x14\n\nYou can [`read`](crate::Reg::read) this register and get [`addr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`addr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@addr`] module"]
#[doc(alias = "ADDR")]
pub type Addr = crate::Reg<addr::AddrSpec>;
#[doc = "FLASH.ADDR, 22 bits at +0x14"]
pub mod addr;
#[doc = "BUF_ADDR (rw) register accessor: FLASH.BUF_ADDR, 12 bits at +0x18\n\nYou can [`read`](crate::Reg::read) this register and get [`buf_addr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`buf_addr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@buf_addr`] module"]
#[doc(alias = "BUF_ADDR")]
pub type BufAddr = crate::Reg<buf_addr::BufAddrSpec>;
#[doc = "FLASH.BUF_ADDR, 12 bits at +0x18"]
pub mod buf_addr;
#[doc = "LEN (rw) register accessor: FLASH.LEN, 13 bits at +0x1a\n\nYou can [`read`](crate::Reg::read) this register and get [`len::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`len::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@len`] module"]
#[doc(alias = "LEN")]
pub type Len = crate::Reg<len::LenSpec>;
#[doc = "FLASH.LEN, 13 bits at +0x1a"]
pub mod len;
#[doc = "RESULT (r) register accessor: FLASH.RESULT, 32 bits at +0x1c\n\nYou can [`read`](crate::Reg::read) this register and get [`result::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@result`] module"]
#[doc(alias = "RESULT")]
pub type Result = crate::Reg<result::ResultSpec>;
#[doc = "FLASH.RESULT, 32 bits at +0x1c"]
pub mod result;
#[doc = "WRITE_LO (rw) register accessor: FLASH.WRITE_LO, 11 bits at +0x20\n\nYou can [`read`](crate::Reg::read) this register and get [`write_lo::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`write_lo::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@write_lo`] module"]
#[doc(alias = "WRITE_LO")]
pub type WriteLo = crate::Reg<write_lo::WriteLoSpec>;
#[doc = "FLASH.WRITE_LO, 11 bits at +0x20"]
pub mod write_lo;
#[doc = "WRITE_HI (rw) register accessor: FLASH.WRITE_HI, 11 bits at +0x22\n\nYou can [`read`](crate::Reg::read) this register and get [`write_hi::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`write_hi::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@write_hi`] module"]
#[doc(alias = "WRITE_HI")]
pub type WriteHi = crate::Reg<write_hi::WriteHiSpec>;
#[doc = "FLASH.WRITE_HI, 11 bits at +0x22"]
pub mod write_hi;
#[doc = "CAL_ADDR (rw) register accessor: FLASH.CAL_ADDR, 22 bits at +0x28\n\nYou can [`read`](crate::Reg::read) this register and get [`cal_addr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`cal_addr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cal_addr`] module"]
#[doc(alias = "CAL_ADDR")]
pub type CalAddr = crate::Reg<cal_addr::CalAddrSpec>;
#[doc = "FLASH.CAL_ADDR, 22 bits at +0x28"]
pub mod cal_addr;
#[doc = "CAL (r) register accessor: FLASH.CAL, 32 bits at +0x2c\n\nYou can [`read`](crate::Reg::read) this register and get [`cal::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cal`] module"]
#[doc(alias = "CAL")]
pub type Cal = crate::Reg<cal::CalSpec>;
#[doc = "FLASH.CAL, 32 bits at +0x2c"]
pub mod cal;
#[doc = "BUSY_CYCLES (r) register accessor: FLASH.BUSY_CYCLES, 29 bits at +0x38\n\nYou can [`read`](crate::Reg::read) this register and get [`busy_cycles::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@busy_cycles`] module"]
#[doc(alias = "BUSY_CYCLES")]
pub type BusyCycles = crate::Reg<busy_cycles::BusyCyclesSpec>;
#[doc = "FLASH.BUSY_CYCLES, 29 bits at +0x38"]
pub mod busy_cycles;
#[doc = "STATE (r) register accessor: FLASH.STATE, 16 bits at +0x3c\n\nYou can [`read`](crate::Reg::read) this register and get [`state::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@state`] module"]
#[doc(alias = "STATE")]
pub type State = crate::Reg<state::StateSpec>;
#[doc = "FLASH.STATE, 16 bits at +0x3c"]
pub mod state;
#[doc = "SEQ (r) register accessor: FLASH.SEQ, 16 bits at +0x3e\n\nYou can [`read`](crate::Reg::read) this register and get [`seq::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@seq`] module"]
#[doc(alias = "SEQ")]
pub type Seq = crate::Reg<seq::SeqSpec>;
#[doc = "FLASH.SEQ, 16 bits at +0x3e"]
pub mod seq;
#[doc = "CK_COUNT (r) register accessor: FLASH.CK_COUNT, 22 bits at +0x40\n\nYou can [`read`](crate::Reg::read) this register and get [`ck_count::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ck_count`] module"]
#[doc(alias = "CK_COUNT")]
pub type CkCount = crate::Reg<ck_count::CkCountSpec>;
#[doc = "FLASH.CK_COUNT, 22 bits at +0x40"]
pub mod ck_count;
#[doc = "FILL_LAST (r) register accessor: FLASH.FILL_LAST, 16 bits at +0x48\n\nYou can [`read`](crate::Reg::read) this register and get [`fill_last::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@fill_last`] module"]
#[doc(alias = "FILL_LAST")]
pub type FillLast = crate::Reg<fill_last::FillLastSpec>;
#[doc = "FLASH.FILL_LAST, 16 bits at +0x48"]
pub mod fill_last;
#[doc = "FILL_MAX (r) register accessor: FLASH.FILL_MAX, 16 bits at +0x4a\n\nYou can [`read`](crate::Reg::read) this register and get [`fill_max::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@fill_max`] module"]
#[doc(alias = "FILL_MAX")]
pub type FillMax = crate::Reg<fill_max::FillMaxSpec>;
#[doc = "FLASH.FILL_MAX, 16 bits at +0x4a"]
pub mod fill_max;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const ID: usize = 0x00;
    pub const STATUS: usize = 0x08;
    pub const CMD: usize = 0x0c;
    pub const CTRL: usize = 0x0d;
    pub const EVENTS: usize = 0x0e;
    pub const IRQ_EN: usize = 0x0f;
    pub const ERRORS: usize = 0x10;
    pub const ADDR: usize = 0x14;
    pub const BUF_ADDR: usize = 0x18;
    pub const LEN: usize = 0x1a;
    pub const RESULT: usize = 0x1c;
    pub const WRITE_LO: usize = 0x20;
    pub const WRITE_HI: usize = 0x22;
    pub const CAL_ADDR: usize = 0x28;
    pub const CAL: usize = 0x2c;
    pub const BUSY_CYCLES: usize = 0x38;
    pub const STATE: usize = 0x3c;
    pub const SEQ: usize = 0x3e;
    pub const CK_COUNT: usize = 0x40;
    pub const FILL_LAST: usize = 0x48;
    pub const FILL_MAX: usize = 0x4a;
}
