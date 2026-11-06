#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    id: Id,
    sck_khz: SckKhz,
    status: Status,
    _reserved3: [u8; 0x02],
    cmd: Cmd,
    ctrl: Ctrl,
    events: Events,
    irq_en: IrqEn,
    errors: Errors,
    _reserved8: [u8; 0x03],
    desc: Desc,
    addr: Addr,
    len: Len,
    flags: Flags,
    _reserved12: [u8; 0x01],
    wait_us: WaitUs,
    poll_us: PollUs,
    cal: Cal,
    steps: Steps,
    _reserved16: [u8; 0x03],
    seq: Seq,
    state: State,
    xip_fills: XipFills,
    cal_us: CalUs,
}
impl RegisterBlock {
    #[doc = "0x00 - SPI0.ID, 32 bits at +0x00"]
    #[inline(always)]
    pub const fn id(&self) -> &Id {
        &self.id
    }
    #[doc = "0x04 - SPI0.SCK_KHZ, 32 bits at +0x04"]
    #[inline(always)]
    pub const fn sck_khz(&self) -> &SckKhz {
        &self.sck_khz
    }
    #[doc = "0x08 - SPI0.STATUS, 16 bits at +0x08"]
    #[inline(always)]
    pub const fn status(&self) -> &Status {
        &self.status
    }
    #[doc = "0x0c - SPI0.CMD, 4 bits at +0x0c"]
    #[inline(always)]
    pub const fn cmd(&self) -> &Cmd {
        &self.cmd
    }
    #[doc = "0x0d - SPI0.CTRL, 1 bits at +0x0d"]
    #[inline(always)]
    pub const fn ctrl(&self) -> &Ctrl {
        &self.ctrl
    }
    #[doc = "0x0e - SPI0.EVENTS, 2 bits at +0x0e"]
    #[inline(always)]
    pub const fn events(&self) -> &Events {
        &self.events
    }
    #[doc = "0x0f - SPI0.IRQ_EN, 2 bits at +0x0f"]
    #[inline(always)]
    pub const fn irq_en(&self) -> &IrqEn {
        &self.irq_en
    }
    #[doc = "0x10 - SPI0.ERRORS, 8 bits at +0x10"]
    #[inline(always)]
    pub const fn errors(&self) -> &Errors {
        &self.errors
    }
    #[doc = "0x14 - SPI0.DESC, 32 bits at +0x14"]
    #[inline(always)]
    pub const fn desc(&self) -> &Desc {
        &self.desc
    }
    #[doc = "0x18 - SPI0.ADDR, 24 bits at +0x18"]
    #[inline(always)]
    pub const fn addr(&self) -> &Addr {
        &self.addr
    }
    #[doc = "0x1c - SPI0.LEN, 9 bits at +0x1c"]
    #[inline(always)]
    pub const fn len(&self) -> &Len {
        &self.len
    }
    #[doc = "0x1e - SPI0.FLAGS, 2 bits at +0x1e"]
    #[inline(always)]
    pub const fn flags(&self) -> &Flags {
        &self.flags
    }
    #[doc = "0x20 - SPI0.WAIT_US, 32 bits at +0x20"]
    #[inline(always)]
    pub const fn wait_us(&self) -> &WaitUs {
        &self.wait_us
    }
    #[doc = "0x24 - SPI0.POLL_US, 32 bits at +0x24"]
    #[inline(always)]
    pub const fn poll_us(&self) -> &PollUs {
        &self.poll_us
    }
    #[doc = "0x28 - SPI0.CAL, 32 bits at +0x28"]
    #[inline(always)]
    pub const fn cal(&self) -> &Cal {
        &self.cal
    }
    #[doc = "0x2c - SPI0.STEPS, 8 bits at +0x2c"]
    #[inline(always)]
    pub const fn steps(&self) -> &Steps {
        &self.steps
    }
    #[doc = "0x30 - SPI0.SEQ, 16 bits at +0x30"]
    #[inline(always)]
    pub const fn seq(&self) -> &Seq {
        &self.seq
    }
    #[doc = "0x32 - SPI0.STATE, 16 bits at +0x32"]
    #[inline(always)]
    pub const fn state(&self) -> &State {
        &self.state
    }
    #[doc = "0x34 - SPI0.XIP_FILLS, 32 bits at +0x34"]
    #[inline(always)]
    pub const fn xip_fills(&self) -> &XipFills {
        &self.xip_fills
    }
    #[doc = "0x38 - SPI0.CAL_US, 16 bits at +0x38"]
    #[inline(always)]
    pub const fn cal_us(&self) -> &CalUs {
        &self.cal_us
    }
}
#[doc = "ID (r) register accessor: SPI0.ID, 32 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`id::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@id`] module"]
#[doc(alias = "ID")]
pub type Id = crate::Reg<id::IdSpec>;
#[doc = "SPI0.ID, 32 bits at +0x00"]
pub mod id;
#[doc = "SCK_KHZ (r) register accessor: SPI0.SCK_KHZ, 32 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`sck_khz::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@sck_khz`] module"]
#[doc(alias = "SCK_KHZ")]
pub type SckKhz = crate::Reg<sck_khz::SckKhzSpec>;
#[doc = "SPI0.SCK_KHZ, 32 bits at +0x04"]
pub mod sck_khz;
#[doc = "STATUS (r) register accessor: SPI0.STATUS, 16 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@status`] module"]
#[doc(alias = "STATUS")]
pub type Status = crate::Reg<status::StatusSpec>;
#[doc = "SPI0.STATUS, 16 bits at +0x08"]
pub mod status;
#[doc = "CMD (w) register accessor: SPI0.CMD, 4 bits at +0x0c\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`cmd::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cmd`] module"]
#[doc(alias = "CMD")]
pub type Cmd = crate::Reg<cmd::CmdSpec>;
#[doc = "SPI0.CMD, 4 bits at +0x0c"]
pub mod cmd;
#[doc = "CTRL (w) register accessor: SPI0.CTRL, 1 bits at +0x0d\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ctrl::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ctrl`] module"]
#[doc(alias = "CTRL")]
pub type Ctrl = crate::Reg<ctrl::CtrlSpec>;
#[doc = "SPI0.CTRL, 1 bits at +0x0d"]
pub mod ctrl;
#[doc = "EVENTS (rw) register accessor: SPI0.EVENTS, 2 bits at +0x0e\n\nYou can [`read`](crate::Reg::read) this register and get [`events::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`events::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@events`] module"]
#[doc(alias = "EVENTS")]
pub type Events = crate::Reg<events::EventsSpec>;
#[doc = "SPI0.EVENTS, 2 bits at +0x0e"]
pub mod events;
#[doc = "IRQ_EN (rw) register accessor: SPI0.IRQ_EN, 2 bits at +0x0f\n\nYou can [`read`](crate::Reg::read) this register and get [`irq_en::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`irq_en::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@irq_en`] module"]
#[doc(alias = "IRQ_EN")]
pub type IrqEn = crate::Reg<irq_en::IrqEnSpec>;
#[doc = "SPI0.IRQ_EN, 2 bits at +0x0f"]
pub mod irq_en;
#[doc = "ERRORS (rw) register accessor: SPI0.ERRORS, 8 bits at +0x10\n\nYou can [`read`](crate::Reg::read) this register and get [`errors::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`errors::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@errors`] module"]
#[doc(alias = "ERRORS")]
pub type Errors = crate::Reg<errors::ErrorsSpec>;
#[doc = "SPI0.ERRORS, 8 bits at +0x10"]
pub mod errors;
#[doc = "DESC (rw) register accessor: SPI0.DESC, 32 bits at +0x14\n\nYou can [`read`](crate::Reg::read) this register and get [`desc::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`desc::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@desc`] module"]
#[doc(alias = "DESC")]
pub type Desc = crate::Reg<desc::DescSpec>;
#[doc = "SPI0.DESC, 32 bits at +0x14"]
pub mod desc;
#[doc = "ADDR (rw) register accessor: SPI0.ADDR, 24 bits at +0x18\n\nYou can [`read`](crate::Reg::read) this register and get [`addr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`addr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@addr`] module"]
#[doc(alias = "ADDR")]
pub type Addr = crate::Reg<addr::AddrSpec>;
#[doc = "SPI0.ADDR, 24 bits at +0x18"]
pub mod addr;
#[doc = "LEN (rw) register accessor: SPI0.LEN, 9 bits at +0x1c\n\nYou can [`read`](crate::Reg::read) this register and get [`len::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`len::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@len`] module"]
#[doc(alias = "LEN")]
pub type Len = crate::Reg<len::LenSpec>;
#[doc = "SPI0.LEN, 9 bits at +0x1c"]
pub mod len;
#[doc = "FLAGS (rw) register accessor: SPI0.FLAGS, 2 bits at +0x1e\n\nYou can [`read`](crate::Reg::read) this register and get [`flags::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`flags::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@flags`] module"]
#[doc(alias = "FLAGS")]
pub type Flags = crate::Reg<flags::FlagsSpec>;
#[doc = "SPI0.FLAGS, 2 bits at +0x1e"]
pub mod flags;
#[doc = "WAIT_US (rw) register accessor: SPI0.WAIT_US, 32 bits at +0x20\n\nYou can [`read`](crate::Reg::read) this register and get [`wait_us::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`wait_us::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@wait_us`] module"]
#[doc(alias = "WAIT_US")]
pub type WaitUs = crate::Reg<wait_us::WaitUsSpec>;
#[doc = "SPI0.WAIT_US, 32 bits at +0x20"]
pub mod wait_us;
#[doc = "POLL_US (r) register accessor: SPI0.POLL_US, 32 bits at +0x24\n\nYou can [`read`](crate::Reg::read) this register and get [`poll_us::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@poll_us`] module"]
#[doc(alias = "POLL_US")]
pub type PollUs = crate::Reg<poll_us::PollUsSpec>;
#[doc = "SPI0.POLL_US, 32 bits at +0x24"]
pub mod poll_us;
#[doc = "CAL (r) register accessor: SPI0.CAL, 32 bits at +0x28\n\nYou can [`read`](crate::Reg::read) this register and get [`cal::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cal`] module"]
#[doc(alias = "CAL")]
pub type Cal = crate::Reg<cal::CalSpec>;
#[doc = "SPI0.CAL, 32 bits at +0x28"]
pub mod cal;
#[doc = "STEPS (r) register accessor: SPI0.STEPS, 8 bits at +0x2c\n\nYou can [`read`](crate::Reg::read) this register and get [`steps::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@steps`] module"]
#[doc(alias = "STEPS")]
pub type Steps = crate::Reg<steps::StepsSpec>;
#[doc = "SPI0.STEPS, 8 bits at +0x2c"]
pub mod steps;
#[doc = "SEQ (r) register accessor: SPI0.SEQ, 16 bits at +0x30\n\nYou can [`read`](crate::Reg::read) this register and get [`seq::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@seq`] module"]
#[doc(alias = "SEQ")]
pub type Seq = crate::Reg<seq::SeqSpec>;
#[doc = "SPI0.SEQ, 16 bits at +0x30"]
pub mod seq;
#[doc = "STATE (r) register accessor: SPI0.STATE, 16 bits at +0x32\n\nYou can [`read`](crate::Reg::read) this register and get [`state::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@state`] module"]
#[doc(alias = "STATE")]
pub type State = crate::Reg<state::StateSpec>;
#[doc = "SPI0.STATE, 16 bits at +0x32"]
pub mod state;
#[doc = "XIP_FILLS (r) register accessor: SPI0.XIP_FILLS, 32 bits at +0x34\n\nYou can [`read`](crate::Reg::read) this register and get [`xip_fills::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@xip_fills`] module"]
#[doc(alias = "XIP_FILLS")]
pub type XipFills = crate::Reg<xip_fills::XipFillsSpec>;
#[doc = "SPI0.XIP_FILLS, 32 bits at +0x34"]
pub mod xip_fills;
#[doc = "CAL_US (r) register accessor: SPI0.CAL_US, 16 bits at +0x38\n\nYou can [`read`](crate::Reg::read) this register and get [`cal_us::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cal_us`] module"]
#[doc(alias = "CAL_US")]
pub type CalUs = crate::Reg<cal_us::CalUsSpec>;
#[doc = "SPI0.CAL_US, 16 bits at +0x38"]
pub mod cal_us;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const ID: usize = 0x00;
    pub const SCK_KHZ: usize = 0x04;
    pub const STATUS: usize = 0x08;
    pub const CMD: usize = 0x0c;
    pub const CTRL: usize = 0x0d;
    pub const EVENTS: usize = 0x0e;
    pub const IRQ_EN: usize = 0x0f;
    pub const ERRORS: usize = 0x10;
    pub const DESC: usize = 0x14;
    pub const ADDR: usize = 0x18;
    pub const LEN: usize = 0x1c;
    pub const FLAGS: usize = 0x1e;
    pub const WAIT_US: usize = 0x20;
    pub const POLL_US: usize = 0x24;
    pub const CAL: usize = 0x28;
    pub const STEPS: usize = 0x2c;
    pub const SEQ: usize = 0x30;
    pub const STATE: usize = 0x32;
    pub const XIP_FILLS: usize = 0x34;
    pub const CAL_US: usize = 0x38;
}
