//! The two Type-C controllers as the shell sees them: configure, service, report.
//!
//! `src/fusb302.rs` knows the part; this knows what the system does with it. The
//! split matters because the interesting behaviour is not in either register
//! write -- it is in *where* the work happens:
//!
//!     interrupt        mask that port's source, record which, return  src/irq.rs
//!     normal context   clear that port's device, re-enable it         here
//!
//! Each `int` line has its own PLIC source, so the handler already knows which
//! device asserted and [`Controllers::service`] services exactly the ports that
//! did. It does NOT read the mux's `LINES` register to find out.
//!
//! That is the reversal in `docs/architecture.md` decision 8. With one shared
//! source this loop had to clear EVERY asserting device before re-enabling, or
//! the level stayed high and the interrupt re-fired immediately -- a storm that
//! presents as a hung CPU, guarded only by a scan being written correctly. One
//! source per device removes the obligation instead of documenting it.
//!
//! What has not changed: servicing still serialises, because one muxed I2C
//! controller means one device at a time. This buys knowing *which*, not
//! concurrency.

use core::fmt::Write;

use crate::bus::Bus;
use crate::clock::{self, Instant};
use crate::events;
use crate::fusb302::{self, Port, State};
use crate::irq;
use crate::uart::Uart;

/// How often the `fault` lines are looked at.
///
/// The same 50 ms the power monitor uses, and for the same reason: it is fast
/// enough that a person who caused the event is still watching, and slow enough
/// to be free -- this poll is one uncached CSR read, not a bus transaction.
///
/// `fault` is polled rather than given a PLIC source of its own, and that stayed
/// true when the `int` lines each got one. Two reasons, and the second is the
/// binding one:
///
///   * It means something different from `int`; mixing them would make a fault
///     distinguishable from an ordinary state change only by a register read,
///     which is the ambiguity `docs/chips/fusb302b-type-c.md` says to avoid.
///   * **Nothing here can clear it.** It drops when the device's fault does. An
///     interrupt on a level the firmware cannot clear has to stay masked until
///     something notices the level has gone -- which is this poll. It would add
///     a handler, a deferral and a re-enable path, and keep the poll.
///
/// `int` is different in exactly that respect: it is clearable, by the three
/// read-to-clear registers `fusb302::clear` reads, so masking terminates.
const FAULT_POLL_MS: u32 = 50;

/// Both controllers' last known state, and whether they have been set up.
pub struct Controllers {
    /// Cleared until [`Controllers::start`] has configured both parts. A failure
    /// leaves it clear and the next `typec` command retries, because a bus that
    /// was not ready at boot should not need a power cycle.
    configured: bool,
    /// What was last reported about each port, so a service that finds nothing
    /// changed says nothing. `None` until the first successful read.
    state: [Option<State>; 2],
    /// When each port's state was last CONFIRMED by a read, which is not when it
    /// last changed.
    ///
    /// Set wherever `state` was read successfully, including when the answer was
    /// the same as before -- `announce` deliberately says nothing in that case,
    /// and a timestamp that only moved on a change would date the reading to the
    /// last event rather than to the last look. `src/board.rs` prints it, and the
    /// difference between the two is what makes an old value readable as "this
    /// port has not moved" instead of as staleness.
    confirmed: [Option<Instant>; 2],
    /// The last `fault` level seen, so only transitions are announced.
    fault: [bool; 2],
    last_poll: Instant,
    /// Interrupts serviced, per port, for the `typec` command. Direct evidence
    /// that the deferral path works end to end: the handler masked, the loop
    /// serviced, the source came back.
    ///
    /// Per port rather than one total, because with a source each the two are
    /// separate facts. One port's count climbing while the other stays at zero
    /// is a statement about which connector something is happening on, which a
    /// single total could not make.
    pub serviced: [u32; 2],
}

impl Controllers {
    pub const fn new() -> Self {
        Controllers {
            configured: false,
            state: [None; 2],
            confirmed: [None; 2],
            fault: [false; 2],
            last_poll: Instant::ZERO,
            serviced: [0; 2],
        }
    }

    /// Configure both controllers so they interrupt on a state change.
    ///
    /// Called once at boot and again by `typec init`. Reports per port, because
    /// the two are independent devices on independent buses and one being absent
    /// says nothing about the other.
    pub fn start(&mut self, uart: &mut Uart, bus: &mut Bus) {
        let mut all = true;
        for port in Port::ALL {
            match fusb302::configure(bus, port) {
                Ok(()) => match fusb302::state(bus, port, fusb302::Bands::Read) {
                    Ok(state) => {
                        self.state[index(port)] = Some(state);
                        self.confirmed[index(port)] = Some(clock::now());
                        crate::log!(
                            uart,
                            "type-c {}: device {:02x}, vbus {}, {}, cc {}",
                            port.name(),
                            state.device_id,
                            if state.vbus { "present" } else { "absent" },
                            state.cc(),
                            state.orientation().name()
                        );
                    }
                    Err(error) => {
                        all = false;
                        crate::log!(uart, "type-c {}: {}", port.name(), error.as_str());
                    }
                },
                Err(error) => {
                    all = false;
                    crate::log!(
                        uart,
                        "type-c {}: configure failed: {}",
                        port.name(),
                        error.as_str()
                    );
                }
            }
        }
        self.configured = all;
    }

    /// Service whichever Type-C ports the handler deferred.
    ///
    /// Called from the main loop on every pass -- not on a timer. The whole
    /// point of the deferral is that the port's source is MASKED between the
    /// handler and this, so the latency here is whatever the loop takes to come
    /// round, and nothing is lost while it does.
    ///
    /// The bitmap comes from the PLIC source that fired, so this services the
    /// port that asserted and does not look at the other one. There is no
    /// `LINES` read here at all: with one source per device there is nothing to
    /// decode, and the register that used to be read to find out is now read
    /// only by `command` and `poll`.
    pub fn service(&mut self, uart: &mut Uart, bus: &mut Bus) {
        let pending = irq::take_type_c();
        if pending == 0 {
            return;
        }
        // A deferred interrupt is being cleared: a millisecond of I2C, which is
        // the longest single thing the main loop does short of a `load`.
        crate::metrics::busy();

        for port in Port::ALL {
            let index = index(port);
            if pending & (1 << index) == 0 {
                continue;
            }
            self.serviced[index] = self.serviced[index].wrapping_add(1);

            match fusb302::clear(bus, port) {
                Err(error) => {
                    // Stamped, like every line nobody asked for: this is the
                    // deferred service path, reached from the main loop rather
                    // than from a typed command.
                    crate::log!(
                        uart,
                        "type-c {}: could not clear: {}",
                        port.name(),
                        error.as_str()
                    );
                }
                Ok(cause) => {
                    // Why the line asserted, said once, on the pass that cleared
                    // it. The three registers are read-to-clear, so this is the
                    // only moment the reason exists anywhere.
                    //
                    // Through the event ring rather than `log!`, even though this
                    // is normal context and a `Uart` is in hand: the record
                    // explains the `TYPE_C_INT` the handler pushed, and two lines
                    // about one interrupt belong in one column and in that order.
                    // The ring also keeps every event's formatting in
                    // `events::report`, beside every other decode instead of in
                    // the middle of this loop. A full ring drops it and counts the
                    // drop, which is the bargain every other event takes.
                    events::push(events::TYPE_C_CAUSE, cause.payload(index) as u64);

                    // An interrupt latched, so something moved: this is the one
                    // place worth paying for a sweep. The poll below does not.
                    match fusb302::state(bus, port, fusb302::Bands::Read) {
                        Ok(state) => {
                            // Dated on every successful read, including one that
                            // found nothing changed -- `announce` says nothing in
                            // that case, and a timestamp that moved only on a
                            // change would date the reading to the last event
                            // rather than to the last look. `board` prints it.
                            self.confirmed[index] = Some(clock::now());
                            self.announce(uart, port, state);
                        }
                        Err(error) => {
                            crate::log!(uart, "type-c {}: {}", port.name(), error.as_str());
                        }
                    }
                }
            }

            // Re-enable even after a failed clear, and re-enable per port. If
            // this device is still asserting -- because it re-asserted while
            // this ran, or because the clear failed -- its interrupt fires
            // again immediately, the handler masks it again, and this runs
            // again. That is a loop with the CPU making progress between
            // iterations, which is the difference between "busy" and "hung".
            // Leaving it masked instead would be a source silently dead for the
            // rest of the session, which is the worse of the two.
            //
            // The other port is untouched throughout: it was never masked.
            irq::resume_type_c(index);
        }
    }

    /// Look at the `fault` lines, and report a change.
    ///
    /// Level, not edge: what is announced is a transition, so a fault that stays
    /// asserted produces one line rather than twenty a second.
    pub fn poll(&mut self, uart: &mut Uart, bus: &Bus) {
        let now = clock::now();
        if self.last_poll.elapsed(now) < clock::millis(FAULT_POLL_MS) {
            return;
        }
        self.last_poll = now;

        let lines = bus.lines();

        for port in Port::ALL {
            let faulting = fusb302::faulting(lines, port);
            if faulting != self.fault[index(port)] {
                self.fault[index(port)] = faulting;
                crate::log!(
                    uart,
                    "type-c {}: fault {}",
                    port.name(),
                    if faulting { "ASSERTED" } else { "cleared" }
                );
            }
        }
    }

    /// What is known about one port without touching the bus, and when it was
    /// learnt.
    ///
    /// The state and its instant are one fact and are returned as one: a caller
    /// that could take the reading without the date could print a value it has no
    /// way to judge. `None` is a controller nothing has ever read -- at boot
    /// before [`Controllers::start`], or on a board whose bus never answered.
    ///
    /// This is what `board` prints. `typec` reads the part instead, because
    /// "what is it now" and "what was last confirmed" are different questions and
    /// the command that touches no bus must answer the second one.
    pub fn cached(&self, port: Port) -> Option<(State, Instant)> {
        match (self.state[index(port)], self.confirmed[index(port)]) {
            (Some(state), Some(at)) => Some((state, at)),
            _ => None,
        }
    }

    /// One line about a port, but only if something about it changed.
    ///
    /// The comparison is against the last ANNOUNCED state, so a port that
    /// interrupts about something this driver does not decode -- a PD message,
    /// a toggle -- does not produce a line saying nothing changed. An interrupt
    /// with no visible consequence is still visible in `typec`'s serviced count.

    /// Fill in bands a `Bands::Skip` read did not measure, from the last one that
    /// did.
    ///
    /// `Skip` returns `None` for both, meaning "not measured" -- which is the
    /// truth about that read and the wrong thing to SHOW, because orientation did
    /// not stop being known just because this particular look did not re-derive
    /// it. Without this, `typec` and `board` would report `cc none` on every call
    /// between attaches.
    ///
    /// Only fills; never overwrites. A read that DID measure is always preferred,
    /// so a cable that moved is reported from the sweep that saw it move.
    fn carry_bands(&self, port: Port, mut state: State) -> State {
        if let Some(previous) = self.state[index(port)] {
            if state.cc1.is_none() {
                state.cc1 = previous.cc1;
            }
            if state.cc2.is_none() {
                state.cc2 = previous.cc2;
            }
        }
        state
    }

    fn announce(&mut self, uart: &mut Uart, port: Port, state: State) {
        if self.state[index(port)] == Some(state) {
            return;
        }
        self.state[index(port)] = Some(state);
        crate::log!(
            uart,
            "type-c {}: vbus {}, {}, cc {}",
            port.name(),
            if state.vbus { "present" } else { "absent" },
            state.cc(),
            state.orientation().name()
        );
    }
}

fn index(port: Port) -> usize {
    match port {
        Port::Target => 0,
        Port::Aux => 1,
    }
}

/// `typec`, or `typec init` to configure both controllers again.
pub fn command(uart: &mut Uart, rest: &[u8], controllers: &mut Controllers, bus: &mut Bus) {
    let rest = crate::shell::parse::trim(rest);
    if rest == b"init" {
        controllers.start(uart, bus);
        return;
    }
    if !rest.is_empty() {
        let _ = writeln!(uart, "usage: typec [init]");
        return;
    }

    let lines = bus.lines();

    let _ = writeln!(
        uart,
        "type-c @{:08x}  lines {:02x}  {}",
        bus.mux_base(),
        lines,
        if controllers.configured {
            "configured"
        } else {
            "NOT configured"
        }
    );

    for port in Port::ALL {
        // Read live rather than reporting the cached state. The cache exists to
        // suppress duplicate log lines; a command that asked "what is it now"
        // and answered from a cache would be answering a different question.
        //
        // Safe to read live, unlike the PAC1954 next door: `state` touches
        // `DEVICE_ID` and `STATUS0`, which have no read side effect and no window
        // around them. The registers that DO -- the three read-to-clear interrupt
        // ones -- have exactly one reader, and it is `service` above.
        // `Skip`: the command asks "what is it now", and VBUS and BC_LVL are read
        // live to answer that. But orientation is not a "now" question -- it moved
        // when the cable moved, and `service` read it then. Sweeping here would
        // re-answer a settled question at the cost of an interrupt per invocation,
        // and `board` and the 50 ms poll would each pay it too.
        match fusb302::state(bus, port, fusb302::Bands::Skip) {
            Ok(state) => {
                let state = controllers.carry_bands(port, state);
                // The orientation and the two bands it was derived from, in that
                // order. The verdict is what a reader wants and the bands are how
                // they check it -- `cc2` beside `0/2` says which pin and why,
                // where `cc2` alone has to be taken on trust. It is UNVALIDATED
                // ON HARDWARE either way: see `fusb302::Orientation`.
                let (cc1, cc2) = state.bands();
                let _ = writeln!(
                    uart,
                    "  {:6} device {:02x}  vbus {:7}  {:22}  cc {:4} {}/{}  int {}  \
                     fault {}  serviced {}",
                    port.name(),
                    state.device_id,
                    if state.vbus { "present" } else { "absent" },
                    state.cc(),
                    state.orientation().name(),
                    cc1,
                    cc2,
                    fusb302::asserting(lines, port) as u8,
                    fusb302::faulting(lines, port) as u8,
                    controllers.serviced[index(port)]
                );
                if state.device_id != 0x91 {
                    // 0x91 is version 9 revision 1, FUSB302B revision B, which
                    // is what both parts on this board read. Anything else means
                    // the bus select reached something other than the intended
                    // controller, or nothing at all -- and an absent device NACKs
                    // rather than returning a wrong id, so this is the narrower
                    // failure of the two.
                    let _ = writeln!(uart, "  {:6} device id is not 0x91", port.name());
                }
            }
            Err(error) => {
                let _ = writeln!(uart, "  {:6} {}", port.name(), error.as_str());
            }
        }
    }
    // Which bus the shared controller is left pointing at. It is not left where
    // this command found it, and nothing depends on where it is left: `Bus`
    // writes the select as part of every transfer, so the value here is a fact
    // about the last transfer and not a mode anything relies on.
    let _ = writeln!(uart, "  bus select now {}", bus.selected());
}
