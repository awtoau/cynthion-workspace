# FUSB302B Driver for USB Power Delivery

[![Crates.io](https://img.shields.io/crates/v/fusb302b.svg)](https://crates.io/crates/fusb302b)
[![Docs.rs](https://docs.rs/fusb302b/badge.svg)](https://docs.rs/fusb302b)
[![CI](https://github.com/okhsunrog/fusb302b/actions/workflows/ci.yml/badge.svg)](https://github.com/okhsunrog/fusb302b/actions/workflows/ci.yml)

This crate provides an `async` driver for the ONSEMI [FUSB302B](https://www.onsemi.com/products/interfaces/usb-type-c-and-power-delivery-controllers/fusb302b), a programmable USB Type-C Controller with Power Delivery (PD).

The driver is designed to work as a PHY (physical layer) driver for a higher-level USB-PD Policy Engine. It achieves this by implementing the `Driver` trait from the **[usbpd-traits](https://crates.io/crates/usbpd-traits)** crate, making it directly compatible with consumers of that trait, such as the [elagil/usbpd](https://github.com/elagil/usbpd) policy engine.

Registers and fields are generated from `device.ddsl` using
[diondokter/device-driver](https://github.com/diondokter/device-driver).

## Hardware validation

Tested on real hardware in **[pinecil-rs](https://github.com/okhsunrog/pinecil-rs)**,
Rust/Embassy firmware for Pinecil V2 (BL706), using the `usbpd` 3.1.0 policy engine.
The FUSB302B shares an I2C bus with the OLED at a nominal 300 kHz and uses its
active-low INT_N pin for asynchronous notifications.

Validated sink operation includes:

- SPR negotiation at 5 V and 20 V.
- EPR entry, chunked source capabilities and a 28 V contract with a 3 A limit.
- Repeated EPR KeepAlive/KeepAliveAck exchanges and automatic renegotiation
  after source-driven power resets. A three-minute capture recorded 472
  KeepAlive/Ack pairs and 180,000 consecutive voltage samples without gaps.

EPR policy and message assembly are provided by `usbpd`; this driver transports
the PD frames. Pinecil leaves the FUSB302B VBUS sense pin disconnected, so these
tests also exercise the external-detector constructor. The heater was off during
validation. See the project's [hardware results and traces](https://github.com/okhsunrog/pinecil-rs/blob/main/docs/epr-bringup.md)
for the test setup and measured scope.

## Features

*   **`async`/.await**: Fully asynchronous, non-blocking operation suitable for use with executors like `embassy`.
*   **PHY Driver**: Implements the `usbpd_traits::Driver` trait, providing a standard interface for sending and receiving USB-PD packets.
*   **Register-Safe**: All register access is defined in a clear `device.ddsl` file and generated at compile time by the `device-driver` crate, preventing illegal register access and providing a type-safe API.
*   **FIFO and IRQ handling**: Packet coding, read-to-clear interrupt servicing and hardware retry/acknowledgement handling.
*   **Policy-independent**: Voltage selection, EPR state and message reassembly stay in the policy engine; the driver owns PHY operations and their waiting budgets.

## Architecture

The PHY stays fully powered during an active PD session (`POWER=0x0f`). This
avoids four extra I2C transactions around every transmission. Automatic
GoodCRC is enabled only after the final initialization reset, so initialization
cannot reset the receiver after acknowledging a new packet.

Reception reads the seven-byte minimum frame (token, header and CRC) together.
Control packets require one FIFO transaction; data packets require two. This
also reduces the time spent draining hardware-generated GoodCRC packets before
handling a following policy message. Keep synchronous logging and unrelated
shared-bus traffic out of time-critical PD request/response paths.

The implementation separates I2C transport (`src/interface.rs`), packet and
FIFO coding (`src/packet.rs`), and PHY sequencing (`src/lib.rs`). Named lengths
and timing budgets describe their purpose; raw register bits are decoded through
the DDSL-generated fieldsets. See [design notes](docs/design.md) for the required
ordering, cancellation limits, CRC handling and GoodCRC revision rules.

## Usage

Add the published driver to your application:

```sh
cargo add fusb302b@0.2.0
```

The four constructors cover polling/IRQ operation and internal/external VBUS
sensing. Initialization requires an attached source on CC1 or CC2; unsupported
hardware versions return `FusbError::UnsupportedVersion`, and no detected
source returns `FusbError::NoCcDetected`. After initialization, pass the driver
to a policy engine implementing the other side of `usbpd_traits::Driver`.

For example, with your HAL's I2C and active-low INT_N pin already initialized:

```rust,ignore
use fusb302b::{Fusb302b, GpioInterrupt};

let driver = Fusb302b::init_with_interrupt_pin(i2c, GpioInterrupt(int_n)).await?;
// Pass `driver` to the application's usbpd policy engine.
```

See the [Pinecil integration](https://github.com/okhsunrog/pinecil-rs/blob/main/src/pd.rs)
for a complete Embassy application using `usbpd` and external VBUS detection.

### Boards without the FUSB302 VBUS sense connection

`init` and `init_with_interrupt_pin` wait for `STATUS0.VBUSOK`. For boards
such as Pinecil V2, whose FUSB302 VBUS pin is not connected, use the existing
external detector API instead:

```rust,ignore
let driver = Fusb302b::init_with_vbus_and_interrupt(
    i2c,
    external_vbus_detector,
    fusb302b::GpioInterrupt(int_n),
).await?;
```

`external_vbus_detector` implements `VbusDetect`. The `()` implementation
returns immediately; it is appropriate only when the board can assume power
is present. It does not measure voltage or detect detach. This board decision
belongs to the application, rather than changing the built-in detector.

### Interrupt and acknowledgement handling

All unmasked interrupt registers (`INTERRUPTA`, `INTERRUPTB`, `INTERRUPT`)
are read and cleared during servicing. Events needed by a later operation
are retained in driver state. RX availability comes from `STATUS1.RX_EMPTY`,
so clearing `I_CRC_CHK` does not discard a queued message.

With `HAS_AUTO_RETRY`, successful `transmit` already means hardware verified
the matching GoodCRC. The PHY also places that frame in RX FIFO; `receive`
consumes it without forwarding it to the policy engine, preserving subsequent
Accept/PS_RDY/data frames. GPIO waits in RX/TX/hard-reset operations are bounded
by their operation deadline, including when no interrupt ever arrives.

## Checks

```sh
bash tools/check.sh
```

CI runs formatting, host regression tests and Clippy for the default, `log`,
`defmt`, `std`, `std,log` and `std,defmt` feature combinations. It checks `no_std`
on `thumbv6m-none-eabi` and `riscv32imc-unknown-none-elf`, builds documentation
with warnings denied, and verifies the publishable package. `log` and `defmt`
are mutually exclusive. Install those two Rust targets to run the full script
locally; `cargo test --locked --test driver` runs just the host regressions.

The tests emulate FIFO traffic, read-to-clear interrupts, missing IRQs, stale TX
completion, reset cleanup, CC orientation and initialization errors. They check
wire coding and all PD object counts, including a queued packet after each frame.
Hardware integration results are recorded separately from these host checks.
See [CHANGELOG.md](CHANGELOG.md) for the 0.2.0 migration notes.

## Acknowledgements

This crate stands on the shoulders of giants and wouldn't be possible without the excellent work of the following projects:

*   **[elagil/usbpd](https://github.com/elagil/usbpd)**: Provides the hardware-agnostic USB-PD policy engine that this driver is designed to serve. The `usbpd-traits` sub-crate provides the essential `Driver` trait that enables this modular design.
*   **[diondokter/device-driver](https://github.com/diondokter/device-driver)**: The code generation toolkit that provides the foundation for safe and structured register access, making this driver significantly more robust and easier to develop.
*   **[fmckeogh/usb-pd-rs](https://github.com/fmckeogh/usb-pd-rs)**: The archived predecessor to the modern `usbpd` ecosystem. This `fusb302b` driver is a ground-up rewrite heavily inspired by the original driver logic found in that repository.

## License

This project is dual-licensed under the [MIT License](LICENSE-MIT) or [Apache License 2.0](LICENSE-APACHE), at your option.
