//! `usbpd` + `fusb302b` on the synchronous shell (#633): the minimum that runs their futures.
//!
//! - One poll executor: [`Pd`] holds the engine future in a static buffer and polls it
//!   from the main loop with a no-op waker. Nothing sleeps, so a waker has nothing to do.
//! - `Bus` is reached through a raw pointer set for the duration of one poll.
//! - Time is the 1 kHz tick `timer::millis()`.

use core::cell::UnsafeCell;
use core::future::Future;
use core::mem::{align_of, size_of_val, MaybeUninit};
use core::pin::Pin;
use core::ptr;
use core::task::{Context, Poll, RawWaker, RawWakerVTable, Waker};

use embassy_time_driver::Driver;
use embedded_hal_async::i2c::{ErrorType, I2c, Operation};
use fusb302b::{Fusb302b, InterruptPin};
use usbpd::protocol_layer::message::data::source_capabilities::SourceCapabilities;
use usbpd::source::device_policy_manager::{CapabilityResponse, DevicePolicyManager};
use usbpd::source::device_policy_manager::{
    DrpDevicePolicyManager, EprDevicePolicyManager, SourceDpm,
};
use usbpd::source::policy_engine::Source;
use usbpd::timers::Timer;

use crate::bus::{self, Bus};
use crate::timer;

/// Bus the FUSB302B on TARGET sits on.
const TARGET_BUS: u8 = bus::BUS_TARGET_C;

struct Cell<T>(UnsafeCell<T>);
// SAFETY: single hart, and every access is from the main loop (never a handler).
unsafe impl<T> Sync for Cell<T> {}

static BUS: Cell<*mut Bus> = Cell(UnsafeCell::new(ptr::null_mut()));
static WRAPS: Cell<(u32, u32)> = Cell(UnsafeCell::new((0, 0)));

struct TimeDriver;

impl Driver for TimeDriver {
    fn now(&self) -> u64 {
        let ms = timer::millis();
        // SAFETY: see `Cell`.
        let (last, high) = unsafe { &mut *WRAPS.0.get() };
        if ms < *last {
            *high += 1;
        }
        *last = ms;
        ((*high as u64) << 32) | ms as u64
    }

    // Polled every pass of the main loop: a deadline is seen on the next poll.
    fn schedule_wake(&self, _at: u64, _waker: &Waker) {}
}

embassy_time_driver::time_driver_impl!(static DRIVER: TimeDriver = TimeDriver);

pub struct SysTimer;

impl Timer for SysTimer {
    fn after_millis(milliseconds: u64) -> impl Future<Output = ()> {
        embassy_time::Timer::after_millis(milliseconds)
    }
}

/// `embedded-hal-async` I2C over the shell's blocking [`Bus`].
pub struct BusI2c;

pub struct I2cError(pub bus::Error);

impl core::fmt::Debug for I2cError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.write_str("i2c")
    }
}

impl embedded_hal_async::i2c::Error for I2cError {
    fn kind(&self) -> embedded_hal_async::i2c::ErrorKind {
        embedded_hal_async::i2c::ErrorKind::Other
    }
}

impl ErrorType for BusI2c {
    type Error = I2cError;
}

impl I2c for BusI2c {
    async fn transaction(
        &mut self,
        address: u8,
        operations: &mut [Operation<'_>],
    ) -> Result<(), Self::Error> {
        // SAFETY: set by `Pd::poll` for exactly the span of one poll.
        let bus = unsafe { &mut *(*BUS.0.get()) };
        let mut register = None;
        for operation in operations {
            match operation {
                Operation::Write(data) => match data.split_first() {
                    Some((&reg, [])) => register = Some(reg),
                    Some((&reg, rest)) => bus
                        .write_registers(TARGET_BUS, address, reg, rest)
                        .map_err(I2cError)?,
                    None => {}
                },
                Operation::Read(data) => {
                    let reg = register.take().unwrap_or(0);
                    bus.read_registers(TARGET_BUS, address, reg, data)
                        .map_err(I2cError)?;
                }
            }
        }
        Ok(())
    }
}

/// Resolves when the part's INT_N is low. The shell's IRQ ring has not been wired yet,
/// so this polls the part through the next I2C read.
pub struct IntPin;

impl InterruptPin for IntPin {
    async fn wait_for_interrupt(&mut self) {
        let mut ready = false;
        core::future::poll_fn(|_| {
            if ready {
                Poll::Ready(())
            } else {
                ready = true;
                Poll::Pending
            }
        })
        .await
    }
}

pub struct Dpm {
    pub milliamps: u16,
}

impl DevicePolicyManager for Dpm {
    fn source_capabilities(&mut self) -> SourceCapabilities {
        SourceCapabilities::new_vsafe5v_only(self.milliamps / 10)
    }

    async fn evaluate_request(
        &mut self,
        _request: &usbpd::protocol_layer::message::data::request::PowerSource,
    ) -> CapabilityResponse {
        CapabilityResponse::Accept
    }
}
impl EprDevicePolicyManager for Dpm {}
impl DrpDevicePolicyManager for Dpm {}
impl SourceDpm for Dpm {}

type Phy = Fusb302b<BusI2c, I2cError, (), IntPin>;
type Engine = Source<Phy, SysTimer, Dpm>;

const SLOT: usize = 3072;

#[repr(align(8))]
struct Slot(UnsafeCell<MaybeUninit<[u8; SLOT]>>);
// SAFETY: see `Cell`.
unsafe impl Sync for Slot {}
static SLOT_BYTES: Slot = Slot(UnsafeCell::new(MaybeUninit::uninit()));

fn noop_waker() -> Waker {
    fn clone(_: *const ()) -> RawWaker {
        RawWaker::new(ptr::null(), &VTABLE)
    }
    fn nop(_: *const ()) {}
    static VTABLE: RawWakerVTable = RawWakerVTable::new(clone, nop, nop, nop);
    // SAFETY: the vtable functions do nothing.
    unsafe { Waker::from_raw(RawWaker::new(ptr::null(), &VTABLE)) }
}

/// Build the PHY, then the engine, then run it. One future, held in [`SLOT_BYTES`].
async fn session(milliamps: u16) {
    let Ok(phy) = Fusb302b::init_with_interrupt_pin(BusI2c, IntPin).await else {
        return;
    };
    let mut engine: Engine = Source::new(phy, Dpm { milliamps }, false);
    let _ = engine.run().await;
}

pub struct Pd {
    future: Option<Held>,
}

struct Held(Pin<&'static mut dyn Future<Output = ()>>);
// SAFETY: one hart; only the main loop (inside `Devices`) touches it.
unsafe impl Send for Held {}

impl Pd {
    pub const fn new() -> Pd {
        Pd { future: None }
    }

    pub fn start(&mut self, milliamps: u16) -> usize {
        let future = session(milliamps);
        const { assert!(align_of::<usize>() <= 8) };
        assert!(size_of_val(&future) <= SLOT);
        let used = size_of_val(&future);
        // SAFETY: SLOT_BYTES is only ever written here, and the previous occupant
        // is dropped by replacing `self.future` first.
        let place = unsafe {
            self.future = None;
            let at = SLOT_BYTES.0.get() as *mut _;
            ptr::write(at, future);
            Pin::new_unchecked(&mut *at as &mut dyn Future<Output = ()>)
        };
        self.future = Some(Held(place));
        used
    }

    pub fn poll(&mut self, bus: &mut Bus) -> bool {
        let Some(Held(future)) = self.future.as_mut() else {
            return false;
        };
        // SAFETY: cleared again before returning.
        unsafe { *BUS.0.get() = bus };
        let waker = noop_waker();
        let done = future.as_mut().poll(&mut Context::from_waker(&waker)).is_ready();
        // SAFETY: as above.
        unsafe { *BUS.0.get() = ptr::null_mut() };
        if done {
            self.future = None;
        }
        !done
    }
}
