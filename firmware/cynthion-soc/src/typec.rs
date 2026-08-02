//! The two Type-C controllers as the shell sees them: configure, service, report.
//!
//! `src/fusb302.rs` knows the part; this knows what the system does with it. The
//! split matters because the interesting behaviour is not in either register
//! write -- it is in *where* the work happens:
//!
//!     interrupt        mask the source, record the event, return   src/irq.rs
//!     normal context   clear every asserting device, re-enable     here
//!
//! and the second half is where the trap in `docs/chips/fusb302b-type-c.md`
//! lives. Both `int` lines are OR-ed onto one PLIC source, that line is a level,
//! and clearing it means an I2C transaction per device. A handler that cleared
//! only the device it expected would leave the other asserting, the line high
//! and the interrupt re-firing immediately -- a storm that presents as a hung
//! CPU. So [`Controllers::service`] iterates over EVERY port whose bit is set,
//! and only then re-enables the source.

use core::fmt::Write;

use crate::bus::Bus;
use crate::clock::{self, Instant};
use crate::fusb302::{self, Port, State};
use crate::irq;
use crate::uart::Uart;

/// How often the `fault` lines are looked at.
///
/// The same 50 ms the power monitor uses, and for the same reason: it is fast
/// enough that a person who caused the event is still watching, and slow enough
/// to be free -- this poll is one uncached CSR read, not a bus transaction.
///
/// `fault` is polled rather than wired to the interrupt deliberately. It means
/// something different from `int`, and mixing them would mean a fault could only
/// be distinguished from an ordinary state change by a register read, which is
/// exactly the ambiguity `docs/chips/fusb302b-type-c.md` says to avoid.
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
    /// The last `fault` level seen, so only transitions are announced.
    fault: [bool; 2],
    last_poll: Instant,
    /// Interrupts serviced, for the `typec` command. Direct evidence that the
    /// deferral path works end to end: the handler masked, the loop serviced,
    /// the source came back.
    pub serviced: u32,
}

impl Controllers {
    pub const fn new() -> Self {
        Controllers {
            configured: false,
            state: [None; 2],
            fault: [false; 2],
            last_poll: Instant::ZERO,
            serviced: 0,
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
                Ok(()) => match fusb302::state(bus, port) {
                    Ok(state) => {
                        self.state[index(port)] = Some(state);
                        let _ = writeln!(uart,
                            "type-c {}: device {:02x}, vbus {}, {}",
                            port.name(), state.device_id,
                            if state.vbus { "present" } else { "absent" },
                            state.cc());
                    }
                    Err(error) => {
                        all = false;
                        let _ = writeln!(uart, "type-c {}: {}", port.name(),
                                         error.as_str());
                    }
                },
                Err(error) => {
                    all = false;
                    let _ = writeln!(uart, "type-c {}: configure failed: {}",
                                     port.name(), error.as_str());
                }
            }
        }
        self.configured = all;
    }

    /// Service a deferred Type-C interrupt, if one is waiting.
    ///
    /// Called from the main loop on every pass -- not on a timer. The whole
    /// point of the deferral is that the source is MASKED between the handler
    /// and this, so the latency here is whatever the loop takes to come round,
    /// and nothing is lost while it does.
    pub fn service(&mut self, uart: &mut Uart, bus: &mut Bus) {
        if !irq::take_type_c() {
            return;
        }
        self.serviced = self.serviced.wrapping_add(1);

        // Read the lines ONCE and act on that picture. Reading per device would
        // let one assert between the two reads, and acting on a snapshot that
        // never existed is how the second device gets left set.
        let lines = bus.lines();

        // EVERY asserting device, not the first one. This is the rule the
        // module comment is about: a device left uncleared holds the shared line
        // high, and since this function re-enables the source at the end, that
        // would be an interrupt that re-fires forever.
        for port in Port::ALL {
            if !fusb302::asserting(lines, port) {
                continue;
            }
            if let Err(error) = fusb302::clear(bus, port) {
                let _ = writeln!(uart, "type-c {}: could not clear: {}",
                                 port.name(), error.as_str());
                // Carry on to the other port anyway. Giving up here would leave
                // the OTHER device asserting as well, turning one unreachable
                // controller into a dead interrupt source.
                continue;
            }
            match fusb302::state(bus, port) {
                Ok(state) => self.announce(uart, port, state),
                Err(error) => {
                    let _ = writeln!(uart, "type-c {}: {}", port.name(),
                                     error.as_str());
                }
            }
        }

        // Only now. If a device is still asserting -- because it re-asserted
        // while this ran, or because the clear failed -- the interrupt fires
        // again immediately, the handler masks again, and this runs again. That
        // is a loop with the CPU making progress between iterations, which is
        // the difference between "busy" and "hung".
        irq::resume_type_c();
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
                let _ = writeln!(uart, "type-c {}: fault {}", port.name(),
                                 if faulting { "ASSERTED" } else { "cleared" });
            }
        }
    }

    /// One line about a port, but only if something about it changed.
    ///
    /// The comparison is against the last ANNOUNCED state, so a port that
    /// interrupts about something this driver does not decode -- a PD message,
    /// a toggle -- does not produce a line saying nothing changed. An interrupt
    /// with no visible consequence is still visible in `typec`'s serviced count.
    fn announce(&mut self, uart: &mut Uart, port: Port, state: State) {
        if self.state[index(port)] == Some(state) {
            return;
        }
        self.state[index(port)] = Some(state);
        let _ = writeln!(uart, "type-c {}: vbus {}, {}", port.name(),
                         if state.vbus { "present" } else { "absent" },
                         state.cc());
    }
}

fn index(port: Port) -> usize {
    match port {
        Port::Target => 0,
        Port::Aux => 1,
    }
}

/// `typec`, or `typec init` to configure both controllers again.
pub fn command(uart: &mut Uart, rest: &[u8], controllers: &mut Controllers,
               bus: &mut Bus) {
    let rest = crate::trim(rest);
    if rest == b"init" {
        controllers.start(uart, bus);
        return;
    }
    if !rest.is_empty() {
        let _ = writeln!(uart, "usage: typec [init]");
        return;
    }

    let lines = bus.lines();

    let _ = writeln!(uart, "type-c @{:08x}  lines {:02x}  irq serviced {}  {}",
                     bus.mux_base(), lines, controllers.serviced,
                     if controllers.configured { "configured" }
                     else { "NOT configured" });

    for port in Port::ALL {
        // Read live rather than reporting the cached state. The cache exists to
        // suppress duplicate log lines; a command that asked "what is it now"
        // and answered from a cache would be answering a different question.
        //
        // Safe to read live, unlike the PAC1954 next door: `state` touches
        // `DEVICE_ID` and `STATUS0`, which have no read side effect and no window
        // around them. The registers that DO -- the three read-to-clear interrupt
        // ones -- have exactly one reader, and it is `service` above.
        match fusb302::state(bus, port) {
            Ok(state) => {
                let _ = writeln!(uart,
                    "  {:6} device {:02x}  vbus {:7}  {:22}  int {}  fault {}",
                    port.name(), state.device_id,
                    if state.vbus { "present" } else { "absent" },
                    state.cc(),
                    fusb302::asserting(lines, port) as u8,
                    fusb302::faulting(lines, port) as u8);
                if state.device_id != 0x91 {
                    // 0x91 is version 9 revision 1, FUSB302B revision B, which
                    // is what both parts on this board read. Anything else means
                    // the bus select reached something other than the intended
                    // controller, or nothing at all -- and an absent device NACKs
                    // rather than returning a wrong id, so this is the narrower
                    // failure of the two.
                    let _ = writeln!(uart, "  {:6} device id is not 0x91",
                                     port.name());
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
