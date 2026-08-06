//! Preemption, and nothing else.
//!
//! Issue #115 asks what preemption alone costs: not RTIC's resource locking, not
//! a monotonic, not a software interrupt controller drained through `msip`. Just
//! the one thing the defect list in `docs/rtic.md` says is a
//! real defect -- that a turn of the main loop is unbounded, and every deferred
//! job waits for it.
//!
//! This is that, hand-written, in one file. A ready bitmap, a threshold, and a
//! table of `fn()`. It is deliberately NOT a scheduler: nothing here blocks,
//! nothing here has a stack, nothing here knows what time it is.
//!
//! ## The mechanism
//!
//! [`pend`] sets a bit. [`run`] is called from the tail of the machine external
//! handler, and for each ready task whose priority is above the one already
//! running it:
//!
//!   1. clears the bit and raises the threshold to that task's priority
//!   2. **sets `mstatus.MIE`** and calls the task
//!   3. clears `mstatus.MIE` and drops the threshold back
//!
//! Step 2 is the whole point. The task runs with interrupts on, so a console
//! byte arriving in the middle of a millisecond of I2C is serviced in the
//! microseconds it takes to trap -- not when that millisecond finishes. The
//! handler nests: the new trap runs the claim loop, pends whatever it found, and
//! calls `run` again, which does nothing unless what it pended outranks the task
//! already running. That threshold is what bounds the nesting to one frame per
//! priority level.
//!
//! ## riscv-rt does not save `mepc`, and that is this file's one sharp edge
//!
//! `_default_start_trap` (riscv-macros 0.4.1, `src/riscv_rt/asm.rs:180-195`)
//! saves the caller-saved GPRs and nothing else: no `mepc`, no `mcause`, no
//! `mstatus`. That is correct for a runtime that never nests, and wrong the
//! instant one does -- the inner trap overwrites `mepc`, and the outer `mret`
//! returns to wherever the inner trap was taken from. The symptom is an
//! interrupted instruction stream that resumes in the wrong place, occasionally,
//! under load.
//!
//! So [`run`] saves and restores `mepc` and `mstatus` around every task. Four
//! CSR accesses per task dispatched; a `csr` is a register access and not a bus
//! transaction, so the cost is in instructions and not in cycles of stall.
//!
//! ## What it does not do
//!
//! No resource locking. Two tasks that share state, or a task that shares state
//! with the main loop, are the caller's problem exactly as they are today. That
//! omission is the measurement: it is what separates "preemption" from "RTIC".

use core::sync::atomic::{AtomicU32, Ordering};

/// Highest priority a task may have. One bit each in [`READY`], so 32 is the
/// ceiling and four is what this firmware has any use for.
pub const LEVELS: usize = 4;

/// The deferred Type-C service. Lowest priority: a millisecond of I2C that
/// nothing is waiting on, and the one job whose whole purpose is to be
/// interruptible.
pub const TASK_TYPE_C: usize = 0;

/// The per-event device work. Above Type-C, because this is the one with a
/// deadline.
pub const TASK_USB: usize = 1;

/// Which tasks are ready, one bit each, bit index == priority.
static READY: AtomicU32 = AtomicU32::new(0);

/// The priority currently running, plus one. Zero means nothing is running, so
/// every task is eligible.
///
/// Plus one rather than a signed "none", so the eligibility test is one shift:
/// a task may run if its bit is at or above this.
static THRESHOLD: AtomicU32 = AtomicU32::new(0);

/// How deep [`run`] has nested, and the worst it has reached. The bound this
/// file claims -- one frame per priority level -- is checkable rather than
/// asserted, and `usb` prints it.
static DEPTH: AtomicU32 = AtomicU32::new(0);
static WORST_DEPTH: AtomicU32 = AtomicU32::new(0);

/// How many task dispatches have happened.
static DISPATCHES: AtomicU32 = AtomicU32::new(0);

/// Instructions retired inside [`run`] that are NOT the task -- the bitmap
/// scan, the threshold, the `mepc`/`mstatus` save and restore, and the two
/// `mstatus.MIE` writes.
///
/// This is the number issue #115 asks for: what preemption alone costs, with
/// the work it schedules subtracted out. Four `csrr`s per dispatch pay for it,
/// which is a register access each and not a bus transaction.
static OVERHEAD: AtomicU32 = AtomicU32::new(0);

/// The table. `fn()` and not a closure: a task takes no arguments and returns
/// nothing, for the same reason an interrupt handler does -- there is nowhere to
/// be handed a context from.
static TASKS: [fn(); LEVELS] = [
    crate::workload::type_c_task,
    crate::workload::usb_task,
    nothing,
    nothing,
];

fn nothing() {}

/// Mark a task ready. Callable from a handler, from a task, or from the shell.
pub fn pend(task: usize) {
    READY.fetch_or(1 << task, Ordering::Release);
}

/// Run every ready task that outranks the one already running.
///
/// Called from the tail of the machine external handler. Returns with
/// `mstatus.MIE` clear and the threshold as it found it, whatever path it took
/// out.
pub fn run() {
    let entered = crate::metrics::minstret();
    let saved = THRESHOLD.load(Ordering::Relaxed);
    let eligible = !0u32 << saved;

    loop {
        let ready = READY.load(Ordering::Acquire) & eligible;
        if ready == 0 {
            break;
        }
        // Highest set bit: the highest-priority ready task.
        let task = 31 - ready.leading_zeros();
        READY.fetch_and(!(1 << task), Ordering::Relaxed);
        THRESHOLD.store(task + 1, Ordering::Relaxed);

        let depth = DEPTH.fetch_add(1, Ordering::Relaxed) + 1;
        WORST_DEPTH.fetch_max(depth, Ordering::Relaxed);
        DISPATCHES.fetch_add(1, Ordering::Relaxed);

        // See the module comment: the nested trap clobbers both.
        let (mepc, mstatus) = save();
        // SAFETY: the threshold above is what stops this task being re-entered
        // by its own source, and every static it touches was initialised before
        // `main`. The trap frame of the handler that called us is already on the
        // stack and is not referenced from here.
        unsafe { riscv::interrupt::enable() };

        OVERHEAD.fetch_add(
            crate::metrics::minstret().wrapping_sub(entered),
            Ordering::Relaxed,
        );
        TASKS[task as usize]();
        let resumed = crate::metrics::minstret();

        riscv::interrupt::disable();
        restore(mepc, mstatus);
        DEPTH.fetch_sub(1, Ordering::Relaxed);
        OVERHEAD.fetch_add(
            crate::metrics::minstret().wrapping_sub(resumed),
            Ordering::Relaxed,
        );
    }

    THRESHOLD.store(saved, Ordering::Relaxed);
}

fn save() -> (usize, usize) {
    let mepc: usize;
    let mstatus: usize;
    // SAFETY: two CSR reads with no side effects.
    unsafe {
        core::arch::asm!("csrr {}, mepc", out(reg) mepc, options(nomem, nostack));
        core::arch::asm!("csrr {}, mstatus", out(reg) mstatus, options(nomem, nostack));
    }
    (mepc, mstatus)
}

fn restore(mepc: usize, mstatus: usize) {
    // SAFETY: both values were read from these registers on this trap, so
    // writing them back cannot produce a state the hardware was not already in.
    // `mstatus` was sampled with `MIE` clear -- hardware clears it on trap entry
    // -- so this also leaves interrupts off, which is what the epilogue expects.
    unsafe {
        core::arch::asm!("csrw mstatus, {}", in(reg) mstatus, options(nomem, nostack));
        core::arch::asm!("csrw mepc, {}", in(reg) mepc, options(nomem, nostack));
    }
}

/// `(dispatches, worst nesting depth, the dispatcher's own instructions)`, for
/// the `usb` report.
pub fn stats() -> (u32, u32, u32) {
    (
        DISPATCHES.load(Ordering::Relaxed),
        WORST_DEPTH.load(Ordering::Relaxed),
        OVERHEAD.load(Ordering::Relaxed),
    )
}
