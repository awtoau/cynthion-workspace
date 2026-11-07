//! `send` and `raw`: build a message, run the exchange, print one `pdx` block.

use core::fmt::Write;

use super::{outcome_text, Pd};
use crate::bus::Bus;
use crate::pd_decode::{self as dec, Decoded, Frame};
use crate::pd_lib::{Op, Outcome};
use crate::pd_verbs::{self as verbs, Class};
use crate::power;
use crate::uart::Uart;

/// What `send`/`raw` refuse before touching the wire.
pub enum Refusal {
    Unknown,
    NeedsForce,
    NeedsIllegal,
    NotRunning,
    Busy,
}

/// Exchange budget, ms: the reply timer, up to 3 chunk requests of 40 ms, the
/// engine's 35 ms quiet window, x1.25 (a poll can land late).
fn budget(timeout: u32) -> u32 {
    (timeout + 3 * 40 + 35) * 5 / 4
}

impl Pd {
    #[allow(clippy::too_many_arguments)]
    pub fn send_named(
        &mut self,
        uart: &mut Uart,
        bus: &mut Bus,
        monitor: &mut power::Monitor,
        name: &str,
        arg: Option<u16>,
        force: bool,
        illegal: bool,
    ) -> Result<(), Refusal> {
        let verb = verbs::find(name).ok_or(Refusal::Unknown)?;
        if verb.destructive && !force {
            return Err(Refusal::NeedsForce);
        }
        if verb.illegal && !illegal {
            return Err(Refusal::NeedsIllegal);
        }
        let frame = verbs::build(verb, arg);
        let timeout = verbs::timeout_ms(verb);
        // Reset and swap verbs include tSrcRecover (0.66-1.0 s, we wait 0.8 s) and a restart.
        let limit = match verb.class {
            Class::HardReset | Class::PrSwap | Class::DrSwap => 3000,
            _ => budget(timeout),
        };
        let op = match verb.class {
            Class::Plain => Op::Send,
            Class::SoftReset => Op::SoftReset,
            Class::HardReset => Op::HardReset,
            Class::DataReset => Op::DataReset,
            Class::DrSwap => Op::DrSwap,
            Class::PrSwap => Op::PrSwap,
        };
        let exchange = Exchange {
            name,
            op,
            sop: verb.sop,
            frame,
            timeout,
            timer_name: verb.timer,
            no_reply: verb.no_reply,
            want: verb.want as usize,
            limit,
        };
        self.run_and_print(uart, bus, monitor, exchange)
    }

    pub fn send_raw(
        &mut self,
        uart: &mut Uart,
        bus: &mut Bus,
        monitor: &mut power::Monitor,
        bytes: &[u8],
        sop: u8,
    ) -> Result<(), Refusal> {
        let frame = Frame::from_wire(sop, bytes).ok_or(Refusal::Unknown)?;
        let exchange = Exchange {
            name: "raw",
            op: Op::Send,
            sop,
            frame,
            timeout: verbs::SENDER_RESPONSE_MS,
            timer_name: verbs::SENDER_RESPONSE,
            no_reply: false,
            want: 1,
            limit: budget(verbs::SENDER_RESPONSE_MS),
        };
        self.run_and_print(uart, bus, monitor, exchange)
    }

    fn run_and_print(
        &mut self,
        uart: &mut Uart,
        bus: &mut Bus,
        monitor: &mut power::Monitor,
        x: Exchange,
    ) -> Result<(), Refusal> {
        if !self.lib.running() {
            return Err(Refusal::NotRunning);
        }
        let caps = [self.lib.caps_pdo()];
        let Some(result) = self.raw(
            uart, bus, monitor, x.op, x.sop, x.frame, x.timeout, x.timer_name, x.no_reply,
            x.want, x.limit,
        ) else {
            return Err(Refusal::Busy);
        };
        let (outcome, detail) = outcome_text(result.outcome);
        let first = if result.n_replies > 0 { result.reply_ms[0] } else { 0 };
        let _ = writeln!(
            uart,
            "pdx name={} sop={} outcome={} latency_ms={} tx_ms={} replies={} detail=\"{}\"",
            x.name,
            dec::sop_name(x.sop),
            outcome,
            first,
            result.tx_ms,
            result.n_replies,
            detail
        );
        if result.outcome != Outcome::NotReady && x.op != Op::HardReset {
            let _ = writeln!(uart, "pdx sent {}", Decoded { frame: &result.sent, caps: &caps });
        }
        for index in 0..result.n_replies {
            let _ = writeln!(
                uart,
                "pdx reply {} +{}ms {}",
                index,
                result.reply_ms[index],
                Decoded { frame: &result.replies[index], caps: &caps }
            );
        }
        if !result.note.is_empty() {
            let _ = writeln!(uart, "pdx note {}", result.note);
        }
        let _ = writeln!(uart, "pdx end");
        Ok(())
    }
}

struct Exchange<'a> {
    name: &'a str,
    op: Op,
    sop: u8,
    frame: Frame,
    timeout: u32,
    timer_name: &'static str,
    no_reply: bool,
    want: usize,
    limit: u32,
}
