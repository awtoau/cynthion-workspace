#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    starts: Starts,
    beats: Beats,
    burst_beats: BurstBeats,
    max_run: MaxRun,
    words: Words,
    _reserved5: [u8; 0x02],
    busy: Busy,
    want: Want,
    arming: Arming,
    cyc: Cyc,
    status: Status,
    _reserved10: [u8; 0x01],
    bursts: Bursts,
    stalls: Stalls,
    clear: Clear,
    sel: Sel,
}
impl RegisterBlock {
    #[doc = "0x00 - HYPERRAM_PROBE.STARTS, 16 bits at +0x00"]
    #[inline(always)]
    pub const fn starts(&self) -> &Starts {
        &self.starts
    }
    #[doc = "0x02 - HYPERRAM_PROBE.BEATS, 16 bits at +0x02"]
    #[inline(always)]
    pub const fn beats(&self) -> &Beats {
        &self.beats
    }
    #[doc = "0x04 - HYPERRAM_PROBE.BURST_BEATS, 16 bits at +0x04"]
    #[inline(always)]
    pub const fn burst_beats(&self) -> &BurstBeats {
        &self.burst_beats
    }
    #[doc = "0x06 - HYPERRAM_PROBE.MAX_RUN, 16 bits at +0x06"]
    #[inline(always)]
    pub const fn max_run(&self) -> &MaxRun {
        &self.max_run
    }
    #[doc = "0x08 - HYPERRAM_PROBE.WORDS, 16 bits at +0x08"]
    #[inline(always)]
    pub const fn words(&self) -> &Words {
        &self.words
    }
    #[doc = "0x0c - HYPERRAM_PROBE.BUSY, 32 bits at +0x0c"]
    #[inline(always)]
    pub const fn busy(&self) -> &Busy {
        &self.busy
    }
    #[doc = "0x10 - HYPERRAM_PROBE.WANT, 32 bits at +0x10"]
    #[inline(always)]
    pub const fn want(&self) -> &Want {
        &self.want
    }
    #[doc = "0x14 - HYPERRAM_PROBE.ARMING, 32 bits at +0x14"]
    #[inline(always)]
    pub const fn arming(&self) -> &Arming {
        &self.arming
    }
    #[doc = "0x18 - HYPERRAM_PROBE.CYC, 32 bits at +0x18"]
    #[inline(always)]
    pub const fn cyc(&self) -> &Cyc {
        &self.cyc
    }
    #[doc = "0x1c - HYPERRAM_PROBE.STATUS, 3 bits at +0x1c"]
    #[inline(always)]
    pub const fn status(&self) -> &Status {
        &self.status
    }
    #[doc = "0x1e - HYPERRAM_PROBE.BURSTS, 16 bits at +0x1e"]
    #[inline(always)]
    pub const fn bursts(&self) -> &Bursts {
        &self.bursts
    }
    #[doc = "0x20 - HYPERRAM_PROBE.STALLS, 32 bits at +0x20"]
    #[inline(always)]
    pub const fn stalls(&self) -> &Stalls {
        &self.stalls
    }
    #[doc = "0x24 - HYPERRAM_PROBE.CLEAR, 1 bits at +0x24"]
    #[inline(always)]
    pub const fn clear(&self) -> &Clear {
        &self.clear
    }
    #[doc = "0x25 - HYPERRAM_PROBE.SEL, 7 bits at +0x25"]
    #[inline(always)]
    pub const fn sel(&self) -> &Sel {
        &self.sel
    }
}
#[doc = "STARTS (r) register accessor: HYPERRAM_PROBE.STARTS, 16 bits at +0x00\n\nYou can [`read`](crate::Reg::read) this register and get [`starts::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@starts`] module"]
#[doc(alias = "STARTS")]
pub type Starts = crate::Reg<starts::StartsSpec>;
#[doc = "HYPERRAM_PROBE.STARTS, 16 bits at +0x00"]
pub mod starts;
#[doc = "BEATS (r) register accessor: HYPERRAM_PROBE.BEATS, 16 bits at +0x02\n\nYou can [`read`](crate::Reg::read) this register and get [`beats::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@beats`] module"]
#[doc(alias = "BEATS")]
pub type Beats = crate::Reg<beats::BeatsSpec>;
#[doc = "HYPERRAM_PROBE.BEATS, 16 bits at +0x02"]
pub mod beats;
#[doc = "BURST_BEATS (r) register accessor: HYPERRAM_PROBE.BURST_BEATS, 16 bits at +0x04\n\nYou can [`read`](crate::Reg::read) this register and get [`burst_beats::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@burst_beats`] module"]
#[doc(alias = "BURST_BEATS")]
pub type BurstBeats = crate::Reg<burst_beats::BurstBeatsSpec>;
#[doc = "HYPERRAM_PROBE.BURST_BEATS, 16 bits at +0x04"]
pub mod burst_beats;
#[doc = "MAX_RUN (r) register accessor: HYPERRAM_PROBE.MAX_RUN, 16 bits at +0x06\n\nYou can [`read`](crate::Reg::read) this register and get [`max_run::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@max_run`] module"]
#[doc(alias = "MAX_RUN")]
pub type MaxRun = crate::Reg<max_run::MaxRunSpec>;
#[doc = "HYPERRAM_PROBE.MAX_RUN, 16 bits at +0x06"]
pub mod max_run;
#[doc = "WORDS (r) register accessor: HYPERRAM_PROBE.WORDS, 16 bits at +0x08\n\nYou can [`read`](crate::Reg::read) this register and get [`words::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@words`] module"]
#[doc(alias = "WORDS")]
pub type Words = crate::Reg<words::WordsSpec>;
#[doc = "HYPERRAM_PROBE.WORDS, 16 bits at +0x08"]
pub mod words;
#[doc = "BUSY (r) register accessor: HYPERRAM_PROBE.BUSY, 32 bits at +0x0c\n\nYou can [`read`](crate::Reg::read) this register and get [`busy::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@busy`] module"]
#[doc(alias = "BUSY")]
pub type Busy = crate::Reg<busy::BusySpec>;
#[doc = "HYPERRAM_PROBE.BUSY, 32 bits at +0x0c"]
pub mod busy;
#[doc = "WANT (r) register accessor: HYPERRAM_PROBE.WANT, 32 bits at +0x10\n\nYou can [`read`](crate::Reg::read) this register and get [`want::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@want`] module"]
#[doc(alias = "WANT")]
pub type Want = crate::Reg<want::WantSpec>;
#[doc = "HYPERRAM_PROBE.WANT, 32 bits at +0x10"]
pub mod want;
#[doc = "ARMING (r) register accessor: HYPERRAM_PROBE.ARMING, 32 bits at +0x14\n\nYou can [`read`](crate::Reg::read) this register and get [`arming::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@arming`] module"]
#[doc(alias = "ARMING")]
pub type Arming = crate::Reg<arming::ArmingSpec>;
#[doc = "HYPERRAM_PROBE.ARMING, 32 bits at +0x14"]
pub mod arming;
#[doc = "CYC (r) register accessor: HYPERRAM_PROBE.CYC, 32 bits at +0x18\n\nYou can [`read`](crate::Reg::read) this register and get [`cyc::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@cyc`] module"]
#[doc(alias = "CYC")]
pub type Cyc = crate::Reg<cyc::CycSpec>;
#[doc = "HYPERRAM_PROBE.CYC, 32 bits at +0x18"]
pub mod cyc;
#[doc = "STATUS (r) register accessor: HYPERRAM_PROBE.STATUS, 3 bits at +0x1c\n\nYou can [`read`](crate::Reg::read) this register and get [`status::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@status`] module"]
#[doc(alias = "STATUS")]
pub type Status = crate::Reg<status::StatusSpec>;
#[doc = "HYPERRAM_PROBE.STATUS, 3 bits at +0x1c"]
pub mod status;
#[doc = "BURSTS (r) register accessor: HYPERRAM_PROBE.BURSTS, 16 bits at +0x1e\n\nYou can [`read`](crate::Reg::read) this register and get [`bursts::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@bursts`] module"]
#[doc(alias = "BURSTS")]
pub type Bursts = crate::Reg<bursts::BurstsSpec>;
#[doc = "HYPERRAM_PROBE.BURSTS, 16 bits at +0x1e"]
pub mod bursts;
#[doc = "STALLS (r) register accessor: HYPERRAM_PROBE.STALLS, 32 bits at +0x20\n\nYou can [`read`](crate::Reg::read) this register and get [`stalls::R`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@stalls`] module"]
#[doc(alias = "STALLS")]
pub type Stalls = crate::Reg<stalls::StallsSpec>;
#[doc = "HYPERRAM_PROBE.STALLS, 32 bits at +0x20"]
pub mod stalls;
#[doc = "CLEAR (w) register accessor: HYPERRAM_PROBE.CLEAR, 1 bits at +0x24\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`clear::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@clear`] module"]
#[doc(alias = "CLEAR")]
pub type Clear = crate::Reg<clear::ClearSpec>;
#[doc = "HYPERRAM_PROBE.CLEAR, 1 bits at +0x24"]
pub mod clear;
#[doc = "SEL (w) register accessor: HYPERRAM_PROBE.SEL, 7 bits at +0x25\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`sel::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@sel`] module"]
#[doc(alias = "SEL")]
pub type Sel = crate::Reg<sel::SelSpec>;
#[doc = "HYPERRAM_PROBE.SEL, 7 bits at +0x25"]
pub mod sel;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const STARTS: usize = 0x00;
    pub const BEATS: usize = 0x02;
    pub const BURST_BEATS: usize = 0x04;
    pub const MAX_RUN: usize = 0x06;
    pub const WORDS: usize = 0x08;
    pub const BUSY: usize = 0x0c;
    pub const WANT: usize = 0x10;
    pub const ARMING: usize = 0x14;
    pub const CYC: usize = 0x18;
    pub const STATUS: usize = 0x1c;
    pub const BURSTS: usize = 0x1e;
    pub const STALLS: usize = 0x20;
    pub const CLEAR: usize = 0x24;
    pub const SEL: usize = 0x25;
}
