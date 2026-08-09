//! `typec` -- the FUSB302B controllers.
//!
//! `crate::typec` is the driver.

use core::fmt::Write;

use crate::bus::Bus;
use crate::shell::parse::trim;
use crate::fusb302::{self, Port};
use crate::typec::*;
use crate::uart::Uart;

/// `typec`, or `typec init` to configure both controllers again.
pub fn command(uart: &mut Uart, rest: &[u8], controllers: &mut Controllers, bus: &mut Bus) {
    let rest = trim(rest);
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

