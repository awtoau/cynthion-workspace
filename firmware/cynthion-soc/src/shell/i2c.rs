//! `i2c` -- scan a bus behind the mux, and `i2c soak` -- hammer one at one rate.
//!
//! Moved out of `main.rs` unchanged (#296). `bus/i2c.rs` is the driver.

use core::fmt::Write;

use crate::shell::parse::{parse_decimal, trim};
use crate::uart::Uart;
use crate::shell::console::board_absent;
use crate::{bus, target, Devices};

/// Scan the power monitor's I2C bus and identify what is on it.
///
/// The scan covers 0x08..0x77 because 0x00..0x07 and 0x78..0x7f are reserved by
/// the I2C specification for general call, ten-bit addressing and the like --
/// probing them can put a device into a mode nobody asked for.
pub(crate) fn command(uart: &mut Uart, rest: &[u8], devices: &mut Devices) {
    // Which of the three buses. There is one controller and three pin-sets, and
    // nothing in a reply says which bus it came from -- both FUSB302Bs answer
    // 0x22 with the same identity byte -- so the bus is named on every call
    // below rather than selected once and remembered.
    let which = trim(rest);
    if which.is_empty() {
        return crate::shell::list_family(uart, "i2c");
    }
    let which: &[u8] = if which == b"status" { b"" } else { which };

    // `i2c soak <bus> <prescale> <reads>` -- find where the bus stops working.
    //
    // **The rate ceiling cannot be computed.** It depends on SDA's rise time,
    // which depends on bus capacitance, which is a property of the copper and
    // is in no datasheet. Arithmetic can say a rate is out of spec; only the
    // board can say whether it works. So this sweeps and counts.
    //
    // It reads the device's IDENTITY every pass, not its address. An address
    // ACK is one bit and a marginal bus gets it right by luck; an identity is
    // sixteen bits that have to be exactly right, from a register read with a
    // repeated START in the middle -- which is the part of the protocol with
    // the tightest setup interval.
    if let Some(args) = which.strip_prefix(b"soak").map(trim) {
        return i2c_soak(uart, args, devices);
    }

    let (bus_select, label) = match which {
        b"" | b"power" => (bus::BUS_POWER_MONITOR, "power_monitor"),
        b"target" => (bus::BUS_TARGET_C, "target_type_c"),
        b"aux" => (bus::BUS_AUX_C, "aux_type_c"),
        _ => {
            let _ = writeln!(uart, "usage: i2c [power|target|aux]");
            let _ = writeln!(uart, "       i2c soak <power|target|aux> <prescale> <reads>");
            return;
        }
    };

    let bus = match devices.bus.as_mut() {
        Some(bus) => bus,
        None => return board_absent(uart),
    };
    // A scan is also how a wedged controller gets recovered, which is why this
    // one command re-initialises. Nothing else does; see `Bus::init`.
    bus.init();

    let _ = writeln!(
        uart,
        "i2c   @{:08x} prescale {} bus {} ({})",
        bus.i2c_base(),
        bus.prescale(),
        bus_select,
        label
    );

    let mut found = [0u8; 8];
    let mut count = 0usize;
    for address in 0x08u8..=0x77 {
        match bus.probe(bus_select, address) {
            Ok(true) => {
                let _ = writeln!(uart, "  {:02x} answers", address);
                if count < found.len() {
                    found[count] = address;
                }
                count += 1;
            }
            Ok(false) => {}
            Err(error) => {
                // Report and stop. A bus that has gone wrong will report the
                // same thing 111 more times, and the first report is the one
                // that says where it happened.
                let _ = writeln!(uart, "  {:02x} {}", address, error.as_str());
                return;
            }
        }
    }
    let _ = writeln!(uart, "  {} device(s)", count);

    // Identify anything that answered. The PAC195x family is what this bus is
    // for, so ask each address whether it is one -- a part that is not simply
    // reports whatever those registers mean to it, which is why the
    // manufacturer id is checked before the product name is trusted.
    for &address in found.iter().take(count.min(found.len())) {
        let mut id = [0u8; 1];
        let manufacturer = match bus.read_registers(
            bus_select,
            address,
            bus::pac195x::REG_MANUFACTURER_ID,
            &mut id,
        ) {
            Ok(()) => id[0],
            Err(error) => {
                let _ = writeln!(uart, "  {:02x} id read failed: {}", address, error.as_str());
                continue;
            }
        };
        if manufacturer != bus::pac195x::MANUFACTURER_MICROCHIP {
            let _ = writeln!(
                uart,
                "  {:02x} manufacturer {:02x}, not a PAC195x",
                address, manufacturer
            );
            continue;
        }
        let mut product = [0u8; 1];
        let mut revision = [0u8; 1];
        let _ = bus.read_registers(
            bus_select,
            address,
            bus::pac195x::REG_PRODUCT_ID,
            &mut product,
        );
        let _ = bus.read_registers(
            bus_select,
            address,
            bus::pac195x::REG_REVISION_ID,
            &mut revision,
        );
        let _ = writeln!(
            uart,
            "  {:02x} {} manufacturer {:02x} revision {:02x}",
            address,
            bus::pac195x::product_name(product[0]),
            manufacturer,
            revision[0]
        );
    }
}

/// `i2c soak <bus> <prescale> <reads>` -- hammer one bus at one rate and count.
///
/// The answer to "will it run faster", obtained rather than derived. Restores
/// the build's own prescale before returning, whatever happens, so a failed
/// experiment does not leave the board on a rate that half works.
pub(crate) fn i2c_soak(uart: &mut Uart, args: &[u8], devices: &mut Devices) {
    let mut field = args.split(|&b| b == b' ').filter(|f| !f.is_empty());
    let (bus_select, label) = match field.next() {
        Some(b"power") => (bus::BUS_POWER_MONITOR, "power_monitor"),
        Some(b"target") => (bus::BUS_TARGET_C, "target_type_c"),
        Some(b"aux") => (bus::BUS_AUX_C, "aux_type_c"),
        _ => {
            let _ = writeln!(uart, "usage: i2c soak <power|target|aux> <prescale> <reads>");
            return;
        }
    };
    let prescale = match field.next().and_then(parse_decimal) {
        Some(value) => value as u16,
        None => {
            let _ = writeln!(uart, "usage: i2c soak <power|target|aux> <prescale> <reads>");
            return;
        }
    };
    let reads = field.next().and_then(parse_decimal).unwrap_or(1000);

    let bus = match devices.bus.as_mut() {
        Some(bus) => bus,
        None => return board_absent(uart),
    };
    let restore = bus.prescale();

    // f_SCL = f_sync / (5 * (PRER + 1)), the formula the bit engine implements.
    let scl_hz = target::TIME_HZ / (5 * (prescale as u32 + 1));
    let _ = writeln!(
        uart,
        "i2c soak  {} at prescale {} = {} Hz scl, {} reads",
        label, prescale, scl_hz, reads
    );

    bus.set_prescale(prescale);

    let address = if bus_select == bus::BUS_POWER_MONITOR { 0x10 } else { 0x22 };
    // The register whose value we know: the PAC1954's manufacturer id, and the
    // FUSB302B's device id. A read that returns the RIGHT value is the check; a
    // read that merely completes proves nothing about timing.
    let register = if bus_select == bus::BUS_POWER_MONITOR { 0xfe } else { 0x01 };

    let mut expected: Option<u8> = None;
    let mut errors = 0u32;
    let mut wrong = 0u32;
    let mut done = 0u32;
    for _ in 0..reads {
        let mut byte = [0u8; 1];
        match bus.read_registers(bus_select, address, register, &mut byte) {
            Ok(()) => {
                match expected {
                    // The first successful read defines the answer, so this
                    // needs no table of device ids and works on any register.
                    None => expected = Some(byte[0]),
                    Some(want) if byte[0] != want => wrong += 1,
                    Some(_) => {}
                }
            }
            Err(_) => errors += 1,
        }
        done += 1;
    }

    bus.set_prescale(restore);

    let _ = writeln!(
        uart,
        "  {} reads  {} bus errors  {} wrong values  expected {:02x}",
        done,
        errors,
        wrong,
        expected.unwrap_or(0)
    );
    // The verdict, stated rather than left to be inferred from two zeroes.
    // ONE failure in a thousand is a failure: this is the rate at which a
    // marginal bus works, and "mostly" is the signature it presents with.
    let _ = writeln!(
        uart,
        "  {} at {} Hz -- prescale restored to {}",
        if errors == 0 && wrong == 0 && expected.is_some() {
            "CLEAN"
        } else {
            "FAILED"
        },
        scl_hz,
        restore
    );
}

