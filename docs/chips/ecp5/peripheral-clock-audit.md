# Every peripheral against the new clock topology

`usb` and `sync` used to come from one PLL and, in practice, ran at the same
rate. [`gateware/soc/clocks.py`](../../../gateware/soc/clocks.py) now takes `usb`
straight from the 60 MHz oscillator on A8, so `sync` has a PLL to itself and is a
free parameter. Three assumptions that were safe under the old topology are not
safe under this one:

1. **`sync == usb`.** Every crossing between them was degenerate and correct by
   accident. They are independent by construction now.
2. **A divisor derived from a constant.** Anything computing a period from a
   hardcoded frequency is wrong by a ratio the moment `sync` moves.
3. **`usb` has no reset.** `ResetSignal("usb")` is tied to 0 — the oscillator
   runs before any PLL is asked for anything, so there is nothing to gate on.

This is the peripheral-by-peripheral review issue #229 asked for. Twenty-four
modules and boundaries, nine findings. A module that turns out to be sound is
recorded with the reason, because the point of the exercise is that none of them
had been looked at since the topology changed.

Related: [`../../soc-clocking.md`](../../soc-clocking.md) §2, the withdrawn
"CPU corrupts above 60 MHz" result whose signature was a `SyncFIFOBuffered`
across two domains — the fault this audit is looking for.

---

## The table

`sync` means the CPU's domain, `usb` the 60 MHz oscillator, `fast` the second
output of the `sync` PLL at 2×, `jtck` a local domain on the JTAG TCK pin.
"Period from" is where a peripheral's cycle counts get their frequency.

| module | domains | crossing mechanism | period from | verdict |
|---|---|---|---|---|
| [`peripherals/stream_buffer.py`](../../../gateware/soc/peripherals/stream_buffer.py) | `i_domain`/`o_domain` | `AsyncFIFOBuffered` when they differ, `SyncFIFOBuffered` when they do not | — | **sound** — the reference pattern |
| [`peripherals/clock_monitor.py`](../../../gateware/soc/peripherals/clock_monitor.py) | `usb` + `sync` | toggle + edge detect (`FFSynchronizer`), `FFSynchronizer` on PLL `LOCK` | `usb`, exact by construction | **sound** |
| [`peripherals/ulpi_window.py`](../../../gateware/soc/peripherals/ulpi_window.py) | `usb` + `sync` | four-phase handshake on two toggles | `usb`, exact by construction | **DEFECT 1** — the timeout reset is a no-op |
| [`peripherals/uart16550.py`](../../../gateware/soc/peripherals/uart16550.py) | `sync` only | none inside; crossing is external and correct | none — no baud generator | **sound** |
| [`peripherals/serial_line.py`](../../../gateware/soc/peripherals/serial_line.py) | `sync` only | `FFSynchronizer` on the rx pad | `divisor` argument | **sound** module, **DEFECT 6** at the call site |
| [`peripherals/i2c_master.py`](../../../gateware/soc/peripherals/i2c_master.py) | `sync` only | `FFSynchronizer` on SDA | `PRER`, written by firmware | **DEFECT 4** |
| [`peripherals/i2c_mux.py`](../../../gateware/soc/peripherals/i2c_mux.py) | `sync` only | four `FFSynchronizer`s on `int`/`fault` | — | **sound** |
| [`peripherals/sideband_csr.py`](../../../gateware/soc/peripherals/sideband_csr.py) | `sync` only | none needed | — | **sound** |
| [`peripherals/vbus_csr.py`](../../../gateware/soc/peripherals/vbus_csr.py) | `sync` only | none needed | — | **sound** |
| [`peripherals/gateware_id.py`](../../../gateware/soc/peripherals/gateware_id.py) | `sync` only | none needed | `2**19` `sync` cycles (DTR cadence) | **sound** — scales harmlessly |
| [`peripherals/flash_cdc.py`](../../../gateware/soc/peripherals/flash_cdc.py) | `sync` + `phy_domain` | two `AsyncFIFOBuffered` + `FFSynchronizer` on `cs` | — | **sound** |
| [`peripherals/flash.py`](../../../gateware/soc/peripherals/flash.py) mmap / crossbar | `domain` param, honoured | none | `MMAP_DEFAULT_TIMEOUT = 256` cycles | **sound** |
| `flash.py` `HoldableSPIController` | `domain` param, **not** honoured | none | — | **DEFECT 7** — latent |
| `flash.py` `ObservablePHY` | `domain` param, honoured | one flop on `dq.i` (upstream) | SCK = `f_domain / 2(1+divisor)` | **sound** |
| `flash.py` `FlashPinProbe` | hardcoded `sync` | **none** | — | **DEFECT 8** — latent |
| `flash.py` `FlashILA` | hardcoded `sync` | incidental 2-stage, per bit | `sample_rate=60e6`, cosmetic | **DEFECT 8** — latent, already documented |
| [`bootram.py`](../../../gateware/soc/bootram.py) (HyperRAM) | `sync` only | JTAG side arrives pre-synchronised | `ck_mhz` argument | **DEFECT 5** |
| [`peripherals/hyperram_dqs_controller.py`](../../../gateware/soc/peripherals/hyperram_dqs_controller.py) | `sync` only | none | `sync_mhz` argument, `ceil`ed | **sound** — dormant |
| [`peripherals/hyperram_dqs_phy.py`](../../../gateware/soc/peripherals/hyperram_dqs_phy.py) | `sync` + `fast` | ECP5 hard 4:1 gearing (`ODDRX2*`/`IDDRX2*`/`TSHX2*`) | `DDRDLL_SETTLE_CYCLES = 8`, cycles by spec | **sound** — dormant; see the note on `LOCK` |
| [`peripherals/hyperram_probe.py`](../../../gateware/soc/peripherals/hyperram_probe.py) | `sync` only | none | — | **sound** |
| [`bus/jtag_stage.py`](../../../gateware/soc/bus/jtag_stage.py) | `jtck` + `sync` | `AsyncFIFO` for the stream, `FFSynchronizer` for `busy` and `cpu_hold` | `JTCK_CONSTRAINT_HZ` (constraint only) | **sound** |
| [`bus/wishbone_pipe.py`](../../../gateware/soc/bus/wishbone_pipe.py) | `sync` only | none — a same-domain pipeline register | latency in cycles | **sound**; see **DEFECT 9** |
| [`cpu/plic.py`](../../../gateware/soc/cpu/plic.py), [`cpu/clint.py`](../../../gateware/soc/cpu/clint.py), [`cpu/cpu.py`](../../../gateware/soc/cpu/cpu.py) | `sync` only | JTAG handled inside VexiiRiscv | `mtime` = 1 `sync` cycle | **sound**, with a firmware coupling |
| console / USB boundary in [`top.py`](../../../gateware/soc/top.py) | `sync` + `usb` | `StreamBuffer` async both ways, `FFSynchronizer` on `usb_took` | — | **sound** data path, **DEFECTS 2 and 3** |

---

## Defect 1 — the ULPI window's timeout reset never fires

**The most serious finding.** `peripherals/ulpi_window.py` bounds a ULPI
transaction and recovers from expiry by holding LUNA's `ULPIRegisterWindow` in
reset for one cycle. Its docstring is precise about why that is the only way
back:

> `ULPIRegisterWindow` waits for `dir` to fall before it may drive the bus […]
> the window has no abort input […] On expiry the window is held in reset for a
> cycle — which is what returns its FSM to IDLE, since it has no other way back
> […] and the next read starts clean.

The reset is applied at `ulpi_window.py:231`:

```python
window = ResetInserter(window_reset)(ULPIRegisterWindow())
```

`ResetInserter` given a bare `Value` means `{"sync": value}` —
`amaranth/hdl/_xfrm.py`, `_ControlInserter.__init__`. Its `on_fragment` then
iterates the victim's statements and **skips every domain not named in that
dict**. `ULPIRegisterWindow` is entirely `usb`: `m.d.usb` throughout and
`with m.FSM(domain="usb")` at `luna/gateware/interface/ulpi.py:97`. It has no
`sync` statements at all, so nothing is inserted and the reset reaches nothing.

Confirmed by simulation rather than by reading — a counter in each domain, the
same `ResetInserter(ctl)` wrapper, `ctl` held high for the whole run:

```
victim logic in `sync`, ResetInserter(ctl) asserted: HELD IN RESET
victim logic in `usb`,  ResetInserter(ctl) asserted: FREE-RUNNING (count=20)
```

**What it costs.** The visible half of the timeout works: the FSM returns to
IDLE, `acknowledge` toggles so firmware is not left waiting, and `status.timeout`
is set. The window itself stays parked in whatever state it was waiting in. The
next request pulses `read_request` at a module that is not in IDLE, `window.done`
never arrives, and that transaction times out too. **One timeout wedges the
peripheral for the life of the bitstream**, and it reports the failure as
"timeout" every time — which reads as an absent PHY rather than as a stuck
window, exactly the misattribution the timeout was written to prevent.

This is not caused by the clocking change; it is a pre-existing fault the review
surfaced. It is listed first because it is unconditional, because it defeats the
recovery path a whole module exists to provide, and because **Defect 2 has
removed the other way out.**

The fix is one argument: `ResetInserter({"usb": window_reset})`. Everything else
in this module — the four-phase toggle handshake, the ordering argument for
carrying `read_data`/`timeout` across without per-bit synchronisers, the bound on
the wait — is correct and is exactly what the new topology requires.

## Defect 2 — both ULPI PHY reset pins are now hardwired de-asserted

`top.py:1602-1605` drives the TARGET PHY's reset pad from the `usb` domain's
reset, and the comment above it argues the case:

> Driving it from `ResetSignal("usb")` means the PHY comes out of reset with the
> domain and is held only while the domain is, which is what a PHY expects;
> **tying it to 0 would leave a PHY that had glitched during configuration with
> no way back.**

`clocks.py:235` now ties `ResetSignal("usb")` to 0. The comment describes the
present behaviour and rejects it.

This is not confined to the port nothing drives. LUNA's `UTMITranslator` does the
same thing for the AUX PHY — the one carrying the USB console —
at `luna/gateware/interface/ulpi.py:857`:

```python
m.d.comb += self.ulpi.rst.o.eq(ResetSignal(raw_clock_domain)),
```

with `raw_clock_domain` defaulting to `usb`. So **neither PHY can be reset by
this design any more.** All three ULPI resources declare `rst_invert=True`, so
the pads simply sit de-asserted.

At cold power-up this changes nothing: the USB3343's own POR completes long
before FPGA configuration does, so the PHY was already running when the fabric
started. The regression is warm reconfiguration and glitch recovery — reflashing
or `trigger_fpga_reconfiguration()` does not power-cycle the PHY (see
[`reconfigure-initn-gap.md`](reconfigure-initn-gap.md)), so a PHY carrying
register state or a stuck bus turn from the previous bitstream now keeps it, with
no reset in the design that can clear it.

Compounded with Defect 1, the TARGET register path has no recovery mechanism at
all: the window cannot reset itself, and the PHY cannot be reset either.

A reset for `usb` cannot come back from PLL lock — that is the dependency the new
topology deliberately removes, and gating a 60 MHz oscillator domain on an
unrelated PLL would be reinventing it. It has to be a deliberate PHY reset: a
short power-on pulse counted in `usb`, a CSR bit, or both. `usb` is exactly
60.000 MHz now, so a pulse counted there is a real duration rather than a guess.

## Defect 3 — the console RX buffer can never be emptied

`StreamBuffer` is the correct pattern and the console uses it correctly
(`top.py:1265-1268`, async both ways). The finding is about reset, not about the
crossing.

Amaranth's `AsyncFIFO` states its rule in the source
(`amaranth/lib/fifo.py`, the "Reset handling" block):

> reset control rests entirely with the write domain. The write domain's reset
> signal is used to asynchronously reset the read domain's counters […] This
> requires the two read domain counters to be marked as `reset_less`.

* `console_tx_buf` is `w_domain="sync"`, `r_domain="usb"`. The write domain has a
  reset, so the inner FIFO empties correctly on a `sync` reset. But
  `AsyncFIFOBuffered`'s output register lives in the *read* domain — `usb`, never
  reset — so one byte captured before the reset is still presented to the
  endpoint after it.
* `console_rx_buf` is `w_domain="usb"`, `r_domain="sync"`. The write domain has
  no reset at all, and the read-domain counters are `reset_less=True`, so
  **`ResetSignal("sync")` does not empty it either.** Bytes the host types while
  `sync` is held in reset waiting for PLL lock survive into the CPU's first read.

Nothing is corrupted — the Gray-code invariant holds precisely because nothing
resets — so this is a behaviour change rather than a data hazard, and it is the
mildest finding here. It is recorded because "the FIFO is cleared when the CPU
resets" is no longer true of the receive path, and a shell that starts by
executing input from before it existed is a confusing first symptom.

## Defect 4 — the I²C bit period is a firmware constant nothing checks

`i2c_master.py` derives everything from `PRER`: one slot per `PRER + 1` `sync`
cycles, five slots per bit, so `f_SCL = f_sync / (5 · (PRER + 1))`. The module is
right; the number is the problem.

`PRER` is written by firmware from `cynthion-soc-pac/src/base.rs`:

```rust
/// I2C prescale for 80 kHz SCL at that clock, from the gateware's own
/// `prescale_for` -- `f_SCL = f_sync / (5 * (PRER + 1))`.
pub const I2C_PRESCALE: u16 = 149;
```

Hand-derived at 60 MHz. At `sync = 130` the same value gives **173 kHz** on a bus
whose timings were chosen to clear standard mode at 80 kHz with margin — the
module docstring's whole argument for 80 rather than 100 is that 100 puts
`t_SU;STA` at 4 µs against a 4.7 µs minimum. Three devices are on that bus: a
PAC1954 and two FUSB302Bs. The failure mode is the one the docstring names, "a
slave which answers most of the time".

Two things make this worse than a stale constant:

* `top.py` imports `prescale_for` (line 107) and defines
  `I2C_SCL_HZ = 80_000` (line 280) and **uses neither**. The correct computation
  is present in the design and is dead code.
* `info` prints `SYNC MISMATCH` when `id.sync_hz != target::TIME_HZ`, and prints
  `CLOCK MISMATCH` when the measured rate disagrees with the declared one. There
  is no equivalent for the prescale, so a `sync` change that *is* reflected in
  `TIME_HZ` still silently rescales the I²C bus.

The cheap fix is to derive `I2C_PRESCALE` in `soc_generate_pac.py` the way
`SYNC_HZ` already is, using the gateware's own `prescale_for(sync_hz,
I2C_SCL_HZ)`. That also makes the two dead references live.

## Defect 5 — the HyperRAM path takes `SYNC_MHZ`, not `actual_sync_mhz`

`clocks.py:148-156` is explicit about which number downstream should read:

> What the hardware will ACTUALLY produce, recomputed from the dividers rather
> than echoed from the request. […] the day that solver learns to approximate,
> everything downstream is already reading the truth.

`GatewareId` (`top.py:828`) and `SidebandDebug` (`top.py:1386`) do read
`car.actual_sync_mhz`, the latter with a comment spelling out the -1.5% baud
error a request/solve mismatch would produce. The HyperRAM does not
(`top.py:1108`):

```python
ck_mhz=2 * SYNC_MHZ if HYPERRAM_DQS else SYNC_MHZ,
```

Everything that turns nanoseconds into cycles downstream of that argument is
computed from the requested frequency: the tCSM burst cap
(`HYPERRAM_TCSM_NS = 4000.0`, `HYPERRAM_TCSM_MARGIN = 0.9`), the tCK-max stall
bound (`HYPERRAM_TCK_MAX_NS = 100.0`), and the DQS controller's tCSHI recovery
(`T_CSHI_NS = 10.0`). The burst cap budgets for it explicitly and the tCSHI count
rounds up, so both err safe; `hyperram_max_stall_cycles` has no margin and
truncates with `int()`.

Latent today, because `solve_pll` only accepts an exact match. It is listed
because it is the one remaining site where the request and the solve are
conflated, and the file whose comment warns about that is two hundred lines away.

Two adjacent HyperRAM observations that are not domain-crossing faults but are
frequency assumptions in the same path:

* `HYPERRAM_LATENCY_CLOCKS = 6` and the DQS controller's
  `HIGH_LATENCY_CLOCKS = 5` track the device's CR0 latency code, which is itself
  a function of CK. Nothing reprograms CR0 as `sync` rises.
* LUNA's `HyperRAMPHY` — the PHY actually built today — hardcodes the CK output
  delay as `p_DEL_VALUE=int(2e-9 / 25e-12)`, a fixed 2 ns against a `sync` period
  that ranges from 15.9 ns down to 7.7 ns.
* ~~With `HYPERRAM_DQS = False` the DQS controller is not built, so **tCSHI is
  enforced by nothing** — LUNA's `HyperRAMInterface.RECOVERY` falls straight
  through to IDLE. The 10 ns gap comes only from the arbiter's FSM round trip,
  which is a fixed cycle count and therefore shrinks in nanoseconds as `sync`
  rises.~~ **Fixed.** Both controllers are vendored now
  (`peripherals/hyperram_controller.py`, `peripherals/hyperram_dqs_controller.py`)
  and both hold tCSHI from a cycle count computed off the caller's `sync_mhz`,
  so the gap follows the clock instead of shrinking under it. This is the one
  entry in this audit that was closed by taking the frequency as a parameter
  rather than by moving a constant.

## Defect 6 — two `sync`-derived periods in `top.py` read the request

Same class as Defect 5, in the top level rather than a peripheral:

* `top.py:1327` — the Apollo UART divisor,
  `int(SYNC_MHZ * 1e6 // APOLLO_UART_BAUD)`.
* `top.py:1433-1435` — the heartbeat LED, `int(SYNC_MHZ * 1e6 // 2)`.

The comment above the first says a design that raises `SYNC_MHZ` "stays correct
by construction". That is true of the *requested* frequency and not of the
*solved* one, which is the distinction the sideband link 60 lines below is
written to respect. The heartbeat is cosmetic; the UART divisor is not — it is
the link to Apollo, and a UART tolerates about ±2%.

Both should read `car.actual_sync_mhz`, for the reason `clocks.py` already gives.

## Defect 7 — `HoldableSPIController` accepts a `domain` it does not honour

`flash.py:587-590` takes `domain="sync"`, stores it, and forwards it to the inner
`SPIController`. Its `elaborate` (612-653) has **no `DomainRenamer`**, and it
elaborates three submodules:

```python
m.submodules.inner       = self._inner        # in self._domain
m.submodules.hold_bridge = self._hold_bridge  # bare `sync`
m.submodules.decoder     = self._decoder      # bare `sync`
```

With `domain="fast"` the `hold` register and its CSR decoder would stay in `sync`
while the inner controller moved, and the `sync`-clocked `hold` flop would be
ORed into `cs` (line 651) and consumed in `fast` with no synchroniser.

Inert today — `top.py:992-993` passes `"sync"`. It is named because it is the
same failure shape as Defect 1: **a domain parameter that is accepted and
silently not applied.** Two instances of that in one tree is a pattern, and the
only defence against it is that somebody read the code.

## Defect 8 — the flash instrumentation is pinned to `sync`

`ObservablePHY` is parameterised on its domain; `FlashPinProbe` and `FlashILA`
are not, and both sample in `sync` unconditionally. With `FLASH_PHY_FAST = True`
every signal they sample is driven in `fast`.

`flash_cdc.py:96-105` already states this for the ILA and reaches the right
conclusion ("The ILA belongs in `phy_domain` with its own bus crossing, or it
belongs retired"). The pin probe has no such note and is worse off: it has no
synchroniser at all (`flash.py:750-754`), and at divisor 0 SCK toggles once per
`fast` cycle — half a `sync` cycle — so its edge counters would be both
metastable and structurally aliased. `grant_ctrl` is `sync` on both sides and
stays valid.

Latent while `FLASH_PHY_FAST = False`. It is exactly what will bite the day
somebody flips that flag to get the flash off the CPU clock — which is the
purpose `flash_cdc.py` was written for.

## Defect 9 — "reachable by the PLL" is not "closeable by the fabric"

Not a domain-crossing fault, but it bounds the premise this audit is written
against. `SocClocks` will accept any `sync_mhz` its solver can hit, and the
design's own recorded measurements say the fabric will not follow:

* `wishbone_pipe.py` exists because the arbiter-to-decoder return path measured
  16.45 ns, and it records the split as leaving "around 8 ns" on each side. A 130
  MHz `sync` is a 7.69 ns period.
* `cpu.py:150-159` records the unrelaxed VexiiRiscv BTB closing at **57.55 MHz**
  — a fail against a 60 MHz constraint — which is why `--relaxed-btb` is a
  permanent generator flag. That measurement was taken at `sync = 60` and has not
  been repeated.

Nothing in the Python checks this. A `SYNC_MHZ` near the top of the reachable
band elaborates cleanly, builds, and fails in nextpnr — or worse, passes
placement on one seed. `ClockMonitor` now reports what the silicon is doing,
which closes the "declared but not running" half of this; the "runs but does not
meet timing" half is still only visible in the build log.

---

## What is sound, and why

Recorded because "we looked and it was fine" is the result issue #229 asked for.

**`stream_buffer.py`** is the correct pattern and the reason this audit has a
reference point. `i_domain`/`o_domain` are explicit at every instantiation site,
so the choice between a synchronous and an asynchronous FIFO is visible where it
is made rather than defaulted.

**`clock_monitor.py`** crosses `usb` → `sync` as a toggle with edge detection and
synchronises the PLL's `LOCK` before the CPU reads it. Its `WINDOW_CYCLES =
60_000` is a hardcoded 60 MHz — which is now *more* correct than it was, because
`usb` is a discrete oscillator rather than a PLL output. Same for
`ulpi_window.py`'s `TIMEOUT_CYCLES = 4096` and LUNA's
`_CYCLES_1_MILLISECONDS = 60000`: all three are `usb` cycle counts that used to
be right by coincidence and are now right by construction.

**`ulpi_window.py`'s crossing** — as distinct from its reset — is right. A
four-phase handshake on two toggles cannot lose a request the way a pulse
synchroniser can, and the argument for carrying `read_data` and `timeout` across
without per-bit synchronisers (write before the toggle, read after it) is sound
ordering rather than an omission.

**`uart16550.py`** is `sync`-only by contract, states so in its docstring, and
has no baud generator at all — the divisor latches are wired to nothing on
purpose. Its two `SyncFIFOBuffered`s have the same domain on both ports; they are
not a crossing. The `ResetInserter(rx_clear)` / `ResetInserter(tx_clear)` pattern
here *does* work, because this module genuinely is in `sync` — which is what
makes Defect 1 a domain mismatch rather than a misuse of the primitive.

**`serial_line.py`**, **`i2c_master.py`** and **`i2c_mux.py`** each own the
`FFSynchronizer` on their asynchronous pin inputs rather than leaving it to the
caller, with `init` values chosen so the reset state is the idle bus (`init=1`
for a UART mark and for SDA, `init=0` for the post-`PinsN` Type-C lines).

**`flash_cdc.py`** is a correct stream crossing: `AsyncFIFOBuffered` both ways
with a documented reason for `Buffered` (a combinational path from the FIFO's
output mux into the PHY's carry chain) and for the depth, plus an
`FFSynchronizer` on `cs` with an explicit statement of when queuing it instead
would be required.

**`jtag_stage.py`** builds a local `jtck` domain on the falling TCK edge, crosses
the payload with an `AsyncFIFO` and both status levels with `FFSynchronizer`s,
and asynchronously resets `jtck` from `sync` so `cpu_hold` is known clear at
power-up with no JTAG attached.

**`plic.py`, `clint.py`, `cpu.py`, `wishbone_pipe.py`, `hyperram_probe.py`,
`sideband_csr.py`, `vbus_csr.py`, `gateware_id.py`, `bootram.py`** are all
single-domain `sync`. None names `usb`; none is affected by the reset change. Two
invariants they depend on are worth writing down because nothing enforces them:
every PLIC source must be driven in `sync` (the PLIC has no synchroniser and
treats sources as combinational levels), and `Clint.mtime` must be driven in
`sync` (it is a bare 64-bit assignment, and a cross-domain one would be
catastrophic). Both hold today.

**`mtime` is a raw `sync` cycle counter** — `cpu.py:313-315`, one tick per edge,
no prescaler — so the machine timebase frequency *is* `SYNC_MHZ`, and the only
thing that keeps firmware honest is regenerating the PAC. `info` already checks
`id.sync_hz != target::TIME_HZ` and says so, which is the right shape; Defect 4
is the same coupling without the check.

**The console data path** at `top.py:1265-1303` is right: async `StreamBuffer`
both ways, `serial.rx.ready` taken from the buffer rather than tied high, and the
one sticky flag set in `usb` and read in `sync` (`usb_took`) goes through an
`FFSynchronizer`. Note that this flag is one-directional and sticky, so the only
hazard is the 0→1 edge — which is what makes two flops sufficient rather than
lucky.

**`hyperram_dqs_phy.py`'s `sync`/`fast` crossing needs no soft CDC.** The ECP5
4:1 gearing primitives are SCLK/ECLK gearboxes that are only legal at exactly 2×
from the same PLL with a defined phase, which is precisely what `SocClocks`
guarantees. Adding synchronisers there would break it. Its use of bare
`ResetSignal()` as `i_RST` on `fast`-clocked primitives is safe only because
`clocks.py` drives both domain resets from the same `~locked` net; that is worth
keeping true.

**`usb` is constrained even though `SocClocks` adds no constraint for it.** The
platform declares `Resource("clk_60MHz", 0, Pins("A8", dir="i"), Clock(60e6), …)`,
so Amaranth attaches the constraint when the resource is requested. This was
checked rather than assumed, because the file's own doctrine is "TELL THE PLACER"
and it visibly calls `add_clock_constraint` for `sync` and `fast` and not for
`usb`.

---

## Stale prose

Not defects, but each describes behaviour the topology change has ended, and each
would mislead a reader:

* `top.py:1594-1598` — argues against tying the PHY reset to 0, which is what now
  happens. See Defect 2.
* `hyperram_dqs_phy.py:37-41` — describes `usb` being solved alongside `sync` by
  `VariableClockDomainGenerator`, which is no longer how `usb` is produced.
* `gateware_id.py:44` and `bootram.py` throughout — still name
  `VariableClockDomainGenerator`.
* `clint.py:87-88` ("16.7 ns"), `clint.py:188` ("9700 years at 60 MHz"),
  `gateware_id.py:77` ("8.7 ms at 60 MHz"), `bootram.py:44` and `:828` — all
  quote a 60 MHz `sync` in prose. Harmless individually; collectively they are
  what makes 60 look like a property of the design rather than one rung.
* `flash.py:43-51` — the divisor table and its 50/62 MHz claims, superseded by
  the measured table in `top.py:520-545` and by
  [`../w25q32-config-flash.md`](../w25q32-config-flash.md).

## Summary

| | count |
|---|---|
| modules and boundaries audited | 24 |
| sound as written | 15 |
| with a named defect | 9 |
| defects that are live today | 4 (1, 2, 3, 4) |
| defects that are latent until a flag flips | 5 (5, 6, 7, 8, 9) |

The one to fix first is **Defect 1**: a peripheral whose recovery path was
written, documented and never applied, in a module whose only other escape hatch
**Defect 2** has just removed.
