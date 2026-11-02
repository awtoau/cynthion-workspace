#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    role: Role,
    ctrl: Ctrl,
    irq_en: IrqEn,
    _reserved3: [u8; 0x01],
    role_status: RoleStatus,
    link: Link,
    events: Events,
    rxpid: Rxpid,
    addr: Addr,
    ep: Ep,
    resp: Resp,
    _reserved10: [u8; 0x01],
    rxlen_lo: RxlenLo,
    rxlen_hi: RxlenHi,
    _reserved12: [u8; 0x02],
    rx_data: RxData,
    rx_tag: RxTag,
    rx_level: RxLevel,
    _reserved15: [u8; 0x01],
    tx_data: TxData,
    tx_last: TxLast,
    tx_cmd: TxCmd,
    tx_level: TxLevel,
    tr_ctrl: TrCtrl,
    tr_state: TrState,
    tr_cnt_lo: TrCntLo,
    tr_cnt_hi: TrCntHi,
    tr_idx_lo: TrIdxLo,
    tr_idx_hi: TrIdxHi,
    tr_sel: TrSel,
    tr_data: TrData,
}
impl RegisterBlock {
    #[doc = "0x00 - USB_HOST.ROLE, 2 bits at +0x00"]
    #[inline(always)]
    pub const fn role(&self) -> &Role {
        &self.role
    }
    #[doc = "0x01 - USB_HOST.CTRL, 4 bits at +0x01"]
    #[inline(always)]
    pub const fn ctrl(&self) -> &Ctrl {
        &self.ctrl
    }
    #[doc = "0x02 - USB_HOST.IRQ_EN, 4 bits at +0x02"]
    #[inline(always)]
    pub const fn irq_en(&self) -> &IrqEn {
        &self.irq_en
    }
    #[doc = "0x04 - USB_HOST.ROLE_STATUS, 4 bits at +0x04"]
    #[inline(always)]
    pub const fn role_status(&self) -> &RoleStatus {
        &self.role_status
    }
    #[doc = "0x05 - USB_HOST.LINK, 8 bits at +0x05"]
    #[inline(always)]
    pub const fn link(&self) -> &Link {
        &self.link
    }
    #[doc = "0x06 - USB_HOST.EVENTS, 8 bits at +0x06"]
    #[inline(always)]
    pub const fn events(&self) -> &Events {
        &self.events
    }
    #[doc = "0x07 - USB_HOST.RXPID, 8 bits at +0x07"]
    #[inline(always)]
    pub const fn rxpid(&self) -> &Rxpid {
        &self.rxpid
    }
    #[doc = "0x08 - USB_HOST.ADDR, 7 bits at +0x08"]
    #[inline(always)]
    pub const fn addr(&self) -> &Addr {
        &self.addr
    }
    #[doc = "0x09 - USB_HOST.EP, 7 bits at +0x09"]
    #[inline(always)]
    pub const fn ep(&self) -> &Ep {
        &self.ep
    }
    #[doc = "0x0a - USB_HOST.RESP, 4 bits at +0x0a"]
    #[inline(always)]
    pub const fn resp(&self) -> &Resp {
        &self.resp
    }
    #[doc = "0x0c - USB_HOST.RXLEN_LO, 8 bits at +0x0c"]
    #[inline(always)]
    pub const fn rxlen_lo(&self) -> &RxlenLo {
        &self.rxlen_lo
    }
    #[doc = "0x0d - USB_HOST.RXLEN_HI, 8 bits at +0x0d"]
    #[inline(always)]
    pub const fn rxlen_hi(&self) -> &RxlenHi {
        &self.rxlen_hi
    }
    #[doc = "0x10 - USB_HOST.RX_DATA, 8 bits at +0x10"]
    #[inline(always)]
    pub const fn rx_data(&self) -> &RxData {
        &self.rx_data
    }
    #[doc = "0x11 - USB_HOST.RX_TAG, 2 bits at +0x11"]
    #[inline(always)]
    pub const fn rx_tag(&self) -> &RxTag {
        &self.rx_tag
    }
    #[doc = "0x12 - USB_HOST.RX_LEVEL, 8 bits at +0x12"]
    #[inline(always)]
    pub const fn rx_level(&self) -> &RxLevel {
        &self.rx_level
    }
    #[doc = "0x14 - USB_HOST.TX_DATA, 8 bits at +0x14"]
    #[inline(always)]
    pub const fn tx_data(&self) -> &TxData {
        &self.tx_data
    }
    #[doc = "0x15 - USB_HOST.TX_LAST, 8 bits at +0x15"]
    #[inline(always)]
    pub const fn tx_last(&self) -> &TxLast {
        &self.tx_last
    }
    #[doc = "0x16 - USB_HOST.TX_CMD, 8 bits at +0x16"]
    #[inline(always)]
    pub const fn tx_cmd(&self) -> &TxCmd {
        &self.tx_cmd
    }
    #[doc = "0x17 - USB_HOST.TX_LEVEL, 8 bits at +0x17"]
    #[inline(always)]
    pub const fn tx_level(&self) -> &TxLevel {
        &self.tx_level
    }
    #[doc = "0x18 - USB_HOST.TR_CTRL, 4 bits at +0x18"]
    #[inline(always)]
    pub const fn tr_ctrl(&self) -> &TrCtrl {
        &self.tr_ctrl
    }
    #[doc = "0x19 - USB_HOST.TR_STATE, 4 bits at +0x19"]
    #[inline(always)]
    pub const fn tr_state(&self) -> &TrState {
        &self.tr_state
    }
    #[doc = "0x1a - USB_HOST.TR_CNT_LO, 8 bits at +0x1a"]
    #[inline(always)]
    pub const fn tr_cnt_lo(&self) -> &TrCntLo {
        &self.tr_cnt_lo
    }
    #[doc = "0x1b - USB_HOST.TR_CNT_HI, 8 bits at +0x1b"]
    #[inline(always)]
    pub const fn tr_cnt_hi(&self) -> &TrCntHi {
        &self.tr_cnt_hi
    }
    #[doc = "0x1c - USB_HOST.TR_IDX_LO, 8 bits at +0x1c"]
    #[inline(always)]
    pub const fn tr_idx_lo(&self) -> &TrIdxLo {
        &self.tr_idx_lo
    }
    #[doc = "0x1d - USB_HOST.TR_IDX_HI, 8 bits at +0x1d"]
    #[inline(always)]
    pub const fn tr_idx_hi(&self) -> &TrIdxHi {
        &self.tr_idx_hi
    }
    #[doc = "0x1e - USB_HOST.TR_SEL, 3 bits at +0x1e"]
    #[inline(always)]
    pub const fn tr_sel(&self) -> &TrSel {
        &self.tr_sel
    }
    #[doc = "0x1f - USB_HOST.TR_DATA, 8 bits at +0x1f"]
    #[inline(always)]
    pub const fn tr_data(&self) -> &TrData {
        &self.tr_data
    }
}
#[doc = "ROLE (rw) register accessor: USB_HOST.ROLE, 2 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`role::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`role::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@role`] module"]
#[doc(alias = "ROLE")]
pub type Role = crate::Reg<role::RoleSpec>;
#[doc = "USB_HOST.ROLE, 2 bits at +0x00"]
pub mod role;
#[doc = "CTRL (w) register accessor: USB_HOST.CTRL, 4 bits at +0x01\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ctrl::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ctrl`] module"]
#[doc(alias = "CTRL")]
pub type Ctrl = crate::Reg<ctrl::CtrlSpec>;
#[doc = "USB_HOST.CTRL, 4 bits at +0x01"]
pub mod ctrl;
#[doc = "IRQ_EN (rw) register accessor: USB_HOST.IRQ_EN, 4 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`irq_en::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`irq_en::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@irq_en`] module"]
#[doc(alias = "IRQ_EN")]
pub type IrqEn = crate::Reg<irq_en::IrqEnSpec>;
#[doc = "USB_HOST.IRQ_EN, 4 bits at +0x02"]
pub mod irq_en;
#[doc = "ROLE_STATUS (r) register accessor: USB_HOST.ROLE_STATUS, 4 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`role_status::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@role_status`] module"]
#[doc(alias = "ROLE_STATUS")]
pub type RoleStatus = crate::Reg<role_status::RoleStatusSpec>;
#[doc = "USB_HOST.ROLE_STATUS, 4 bits at +0x04"]
pub mod role_status;
#[doc = "LINK (r) register accessor: USB_HOST.LINK, 8 bits at +0x05\n\nYou can [`read`](crate::Reg::read) this register and get [`link::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@link`] module"]
#[doc(alias = "LINK")]
pub type Link = crate::Reg<link::LinkSpec>;
#[doc = "USB_HOST.LINK, 8 bits at +0x05"]
pub mod link;
#[doc = "EVENTS (rw) register accessor: USB_HOST.EVENTS, 8 bits at +0x06\n\nYou can [`read`](crate::Reg::read) this register and get [`events::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`events::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@events`] module"]
#[doc(alias = "EVENTS")]
pub type Events = crate::Reg<events::EventsSpec>;
#[doc = "USB_HOST.EVENTS, 8 bits at +0x06"]
pub mod events;
#[doc = "RXPID (r) register accessor: USB_HOST.RXPID, 8 bits at +0x07\n\nYou can [`read`](crate::Reg::read) this register and get [`rxpid::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@rxpid`] module"]
#[doc(alias = "RXPID")]
pub type Rxpid = crate::Reg<rxpid::RxpidSpec>;
#[doc = "USB_HOST.RXPID, 8 bits at +0x07"]
pub mod rxpid;
#[doc = "ADDR (rw) register accessor: USB_HOST.ADDR, 7 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`addr::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`addr::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@addr`] module"]
#[doc(alias = "ADDR")]
pub type Addr = crate::Reg<addr::AddrSpec>;
#[doc = "USB_HOST.ADDR, 7 bits at +0x08"]
pub mod addr;
#[doc = "EP (rw) register accessor: USB_HOST.EP, 7 bits at +0x09\n\nYou can [`read`](crate::Reg::read) this register and get [`ep::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`ep::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@ep`] module"]
#[doc(alias = "EP")]
pub type Ep = crate::Reg<ep::EpSpec>;
#[doc = "USB_HOST.EP, 7 bits at +0x09"]
pub mod ep;
#[doc = "RESP (r) register accessor: USB_HOST.RESP, 4 bits at +0x0a\n\nYou can [`read`](crate::Reg::read) this register and get [`resp::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@resp`] module"]
#[doc(alias = "RESP")]
pub type Resp = crate::Reg<resp::RespSpec>;
#[doc = "USB_HOST.RESP, 4 bits at +0x0a"]
pub mod resp;
#[doc = "RXLEN_LO (r) register accessor: USB_HOST.RXLEN_LO, 8 bits at +0x0c\n\nYou can [`read`](crate::Reg::read) this register and get [`rxlen_lo::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@rxlen_lo`] module"]
#[doc(alias = "RXLEN_LO")]
pub type RxlenLo = crate::Reg<rxlen_lo::RxlenLoSpec>;
#[doc = "USB_HOST.RXLEN_LO, 8 bits at +0x0c"]
pub mod rxlen_lo;
#[doc = "RXLEN_HI (r) register accessor: USB_HOST.RXLEN_HI, 8 bits at +0x0d\n\nYou can [`read`](crate::Reg::read) this register and get [`rxlen_hi::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@rxlen_hi`] module"]
#[doc(alias = "RXLEN_HI")]
pub type RxlenHi = crate::Reg<rxlen_hi::RxlenHiSpec>;
#[doc = "USB_HOST.RXLEN_HI, 8 bits at +0x0d"]
pub mod rxlen_hi;
#[doc = "RX_DATA (rw) register accessor: USB_HOST.RX_DATA, 8 bits at +0x10\n\nYou can [`read`](crate::Reg::read) this register and get [`rx_data::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`rx_data::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@rx_data`] module"]
#[doc(alias = "RX_DATA")]
pub type RxData = crate::Reg<rx_data::RxDataSpec>;
#[doc = "USB_HOST.RX_DATA, 8 bits at +0x10"]
pub mod rx_data;
#[doc = "RX_TAG (r) register accessor: USB_HOST.RX_TAG, 2 bits at +0x11\n\nYou can [`read`](crate::Reg::read) this register and get [`rx_tag::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@rx_tag`] module"]
#[doc(alias = "RX_TAG")]
pub type RxTag = crate::Reg<rx_tag::RxTagSpec>;
#[doc = "USB_HOST.RX_TAG, 2 bits at +0x11"]
pub mod rx_tag;
#[doc = "RX_LEVEL (r) register accessor: USB_HOST.RX_LEVEL, 8 bits at +0x12\n\nYou can [`read`](crate::Reg::read) this register and get [`rx_level::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@rx_level`] module"]
#[doc(alias = "RX_LEVEL")]
pub type RxLevel = crate::Reg<rx_level::RxLevelSpec>;
#[doc = "USB_HOST.RX_LEVEL, 8 bits at +0x12"]
pub mod rx_level;
#[doc = "TX_DATA (w) register accessor: USB_HOST.TX_DATA, 8 bits at +0x14\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tx_data::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@tx_data`] module"]
#[doc(alias = "TX_DATA")]
pub type TxData = crate::Reg<tx_data::TxDataSpec>;
#[doc = "USB_HOST.TX_DATA, 8 bits at +0x14"]
pub mod tx_data;
#[doc = "TX_LAST (w) register accessor: USB_HOST.TX_LAST, 8 bits at +0x15\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tx_last::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@tx_last`] module"]
#[doc(alias = "TX_LAST")]
pub type TxLast = crate::Reg<tx_last::TxLastSpec>;
#[doc = "USB_HOST.TX_LAST, 8 bits at +0x15"]
pub mod tx_last;
#[doc = "TX_CMD (w) register accessor: USB_HOST.TX_CMD, 8 bits at +0x16\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tx_cmd::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@tx_cmd`] module"]
#[doc(alias = "TX_CMD")]
pub type TxCmd = crate::Reg<tx_cmd::TxCmdSpec>;
#[doc = "USB_HOST.TX_CMD, 8 bits at +0x16"]
pub mod tx_cmd;
#[doc = "TX_LEVEL (r) register accessor: USB_HOST.TX_LEVEL, 8 bits at +0x17\n\nYou can [`read`](crate::Reg::read) this register and get [`tx_level::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@tx_level`] module"]
#[doc(alias = "TX_LEVEL")]
pub type TxLevel = crate::Reg<tx_level::TxLevelSpec>;
#[doc = "USB_HOST.TX_LEVEL, 8 bits at +0x17"]
pub mod tx_level;
#[doc = "TR_CTRL (w) register accessor: USB_HOST.TR_CTRL, 4 bits at +0x18\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tr_ctrl::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@tr_ctrl`] module"]
#[doc(alias = "TR_CTRL")]
pub type TrCtrl = crate::Reg<tr_ctrl::TrCtrlSpec>;
#[doc = "USB_HOST.TR_CTRL, 4 bits at +0x18"]
pub mod tr_ctrl;
#[doc = "TR_STATE (r) register accessor: USB_HOST.TR_STATE, 4 bits at +0x19\n\nYou can [`read`](crate::Reg::read) this register and get [`tr_state::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@tr_state`] module"]
#[doc(alias = "TR_STATE")]
pub type TrState = crate::Reg<tr_state::TrStateSpec>;
#[doc = "USB_HOST.TR_STATE, 4 bits at +0x19"]
pub mod tr_state;
#[doc = "TR_CNT_LO (r) register accessor: USB_HOST.TR_CNT_LO, 8 bits at +0x1a\n\nYou can [`read`](crate::Reg::read) this register and get [`tr_cnt_lo::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@tr_cnt_lo`] module"]
#[doc(alias = "TR_CNT_LO")]
pub type TrCntLo = crate::Reg<tr_cnt_lo::TrCntLoSpec>;
#[doc = "USB_HOST.TR_CNT_LO, 8 bits at +0x1a"]
pub mod tr_cnt_lo;
#[doc = "TR_CNT_HI (r) register accessor: USB_HOST.TR_CNT_HI, 8 bits at +0x1b\n\nYou can [`read`](crate::Reg::read) this register and get [`tr_cnt_hi::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@tr_cnt_hi`] module"]
#[doc(alias = "TR_CNT_HI")]
pub type TrCntHi = crate::Reg<tr_cnt_hi::TrCntHiSpec>;
#[doc = "USB_HOST.TR_CNT_HI, 8 bits at +0x1b"]
pub mod tr_cnt_hi;
#[doc = "TR_IDX_LO (rw) register accessor: USB_HOST.TR_IDX_LO, 8 bits at +0x1c\n\nYou can [`read`](crate::Reg::read) this register and get [`tr_idx_lo::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tr_idx_lo::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@tr_idx_lo`] module"]
#[doc(alias = "TR_IDX_LO")]
pub type TrIdxLo = crate::Reg<tr_idx_lo::TrIdxLoSpec>;
#[doc = "USB_HOST.TR_IDX_LO, 8 bits at +0x1c"]
pub mod tr_idx_lo;
#[doc = "TR_IDX_HI (rw) register accessor: USB_HOST.TR_IDX_HI, 8 bits at +0x1d\n\nYou can [`read`](crate::Reg::read) this register and get [`tr_idx_hi::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tr_idx_hi::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@tr_idx_hi`] module"]
#[doc(alias = "TR_IDX_HI")]
pub type TrIdxHi = crate::Reg<tr_idx_hi::TrIdxHiSpec>;
#[doc = "USB_HOST.TR_IDX_HI, 8 bits at +0x1d"]
pub mod tr_idx_hi;
#[doc = "TR_SEL (rw) register accessor: USB_HOST.TR_SEL, 3 bits at +0x1e\n\nYou can [`read`](crate::Reg::read) this register and get [`tr_sel::R`]. You can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`tr_sel::W`]. You can also [`modify`](crate::Reg::modify) this register. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@tr_sel`] module"]
#[doc(alias = "TR_SEL")]
pub type TrSel = crate::Reg<tr_sel::TrSelSpec>;
#[doc = "USB_HOST.TR_SEL, 3 bits at +0x1e"]
pub mod tr_sel;
#[doc = "TR_DATA (r) register accessor: USB_HOST.TR_DATA, 8 bits at +0x1f\n\nYou can [`read`](crate::Reg::read) this register and get [`tr_data::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@tr_data`] module"]
#[doc(alias = "TR_DATA")]
pub type TrData = crate::Reg<tr_data::TrDataSpec>;
#[doc = "USB_HOST.TR_DATA, 8 bits at +0x1f"]
pub mod tr_data;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const ROLE: usize = 0x00;
    pub const CTRL: usize = 0x01;
    pub const IRQ_EN: usize = 0x02;
    pub const ROLE_STATUS: usize = 0x04;
    pub const LINK: usize = 0x05;
    pub const EVENTS: usize = 0x06;
    pub const RXPID: usize = 0x07;
    pub const ADDR: usize = 0x08;
    pub const EP: usize = 0x09;
    pub const RESP: usize = 0x0a;
    pub const RXLEN_LO: usize = 0x0c;
    pub const RXLEN_HI: usize = 0x0d;
    pub const RX_DATA: usize = 0x10;
    pub const RX_TAG: usize = 0x11;
    pub const RX_LEVEL: usize = 0x12;
    pub const TX_DATA: usize = 0x14;
    pub const TX_LAST: usize = 0x15;
    pub const TX_CMD: usize = 0x16;
    pub const TX_LEVEL: usize = 0x17;
    pub const TR_CTRL: usize = 0x18;
    pub const TR_STATE: usize = 0x19;
    pub const TR_CNT_LO: usize = 0x1a;
    pub const TR_CNT_HI: usize = 0x1b;
    pub const TR_IDX_LO: usize = 0x1c;
    pub const TR_IDX_HI: usize = 0x1d;
    pub const TR_SEL: usize = 0x1e;
    pub const TR_DATA: usize = 0x1f;
}
