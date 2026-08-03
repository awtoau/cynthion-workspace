#[repr(C)]
#[doc = "Register block"]
pub struct RegisterBlock {
    starts: Starts,
    beats: Beats,
    burst_beats: BurstBeats,
    max_run: MaxRun,
    clear: Clear,
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
    #[doc = "0x08 - HYPERRAM_PROBE.CLEAR, 1 bits at +0x08"]
    #[inline(always)]
    pub const fn clear(&self) -> &Clear {
        &self.clear
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
#[doc = "CLEAR (w) register accessor: HYPERRAM_PROBE.CLEAR, 1 bits at +0x08\n\nYou can [`reset`](crate::Reg::reset), [`write`](crate::Reg::write), [`write_with_zero`](crate::Reg::write_with_zero) this register using [`clear::W`]. See [API](https://docs.rs/svd2rust/#read--modify--write-api).\n\nFor information about available fields see [`mod@clear`] module"]
#[doc(alias = "CLEAR")]
pub type Clear = crate::Reg<clear::ClearSpec>;
#[doc = "HYPERRAM_PROBE.CLEAR, 1 bits at +0x08"]
pub mod clear;

/// Byte offsets from this peripheral's generated base address.
pub mod offset {
    pub const STARTS: usize = 0x00;
    pub const BEATS: usize = 0x02;
    pub const BURST_BEATS: usize = 0x04;
    pub const MAX_RUN: usize = 0x06;
    pub const CLEAR: usize = 0x08;
}
