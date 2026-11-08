//! Port-level operations over the whole board, shared by the shell and the op
//! protocol (#303): the side effects that belong to a port, not to a front end.
//!
//! `vbus.rs` is the switch driver; this adds what the front ends both did by
//! hand around it (arming PD on TARGET-C, re-configuring the CC controller).

use crate::fusb302::{self, HostCurrent, Port};
use crate::vbus::{self, On, PowerError, Source};
use crate::vbus_gate::TARGET_C;
use crate::Devices;

/// The Type-C controller on `port` (a `power::PORTS` index), if it has one.
pub fn controller(port: usize) -> Option<Port> {
    match port {
        1 => Some(Port::Target),
        2 => Some(Port::Aux),
        _ => None,
    }
}

/// The source whose switch feeds the shared rail from `port`.
pub fn source(port: usize) -> Option<Source> {
    match port {
        1 => Some(Source::TargetC),
        2 => Some(Source::Aux),
        3 => Some(Source::Control),
        _ => None,
    }
}

/// Power `port` on; TARGET-C also arms its PD session.
pub fn power_on(devices: &mut Devices, port: usize, current: HostCurrent) -> Result<On, PowerError> {
    let on = vbus::power_on(port, current, &mut devices.power, devices.bus.as_mut())?;
    if port == TARGET_C {
        devices.type_c.pd.arm(current);
    }
    Ok(on)
}

/// Stop driving `port`; TARGET-C also disarms PD.
pub fn power_off(devices: &mut Devices, port: usize) -> Result<(), PowerError> {
    vbus::power_off(port, &mut devices.power, devices.bus.as_mut())?;
    if port == TARGET_C {
        devices.type_c.pd.disarm();
    }
    Ok(())
}

/// `vbus off`: open every switch, disarm the gate limits, put TARGET's CC back.
pub fn open_all(devices: &mut Devices) {
    vbus::open_all();
    if let Some(bus) = devices.bus.as_mut() {
        devices.power.vbus_disarm(bus);
        let _ = fusb302::configure(bus, Port::Target);
    }
}
