# Proposed issues after the first USB host increment

Drafts only. Nothing filed; `awtoau/cynthion-workspace` is private, but these are
proposals for the owner rather than actions taken.

---

## Draft 1 — `target_phy` has two owners, and the fix is re-parenting

**Blocks:** any host-mode bitstream. **Also unblocks:** #120, #125.

`platform.request("target_phy")` may be called once, and
`gateware/soc/top.py` already calls it, driving `clk`, `rst`,
`stp`, `data.o` and `data.oe` combinationally from `UlpiRegisters`. A host engine
needs the same pins, and there is no mux point.

Arbitration is the wrong fix, for a reason more basic than contention:
`peripherals/ulpi_window.py` hard-wires `data_oe = ~dir_i` and waits for `dir` to fall before
driving. A host engine receiving a packet holds `dir` high, so the window's
4096-cycle timeout fires and a working system reports a broken PHY.

LUNA's `UTMITranslator` — which `USBSIE` instantiates — already contains a
`ULPIRegisterWindow`. So the fix is to expose the one that is there rather than
add a second master. Its docstring already promises `address`, `read_data`,
`write_data`, `manual_read` and `manual_write`; `__init__` does not create them,
and `ULPIControlTranslator` has a free `m.Else()` branch that is where a
CPU-driven access goes. That is a small upstreamable patch against LUNA:
**upstream documents an interface it does not provide.**

Acceptance: `UlpiRegisters` reads the USB3343's registers through the translator,
with the host engine instantiated on the same PHY, in simulation.

---

## Draft 2 — the in-situ area and fmax of the host engine

`scripts/usb_host_area.py` measures the engine standalone: 2080 LUT, 434 FF, 0
BRAM, 96 LUTRAM, 125.55 MHz against a 60 MHz target. That is at about 10%
occupancy. `docs/usb-host-options.md` §12.3 records that this design's critical
path is routing-dominated and that placement varies by roughly 9 MHz between
runs, so the figure that decides the design is the one at ~60% occupancy inside
`AwtoSoc`, alongside `scripts/soc_timing_sweep.py`.

Depends on draft 1: the engine cannot be instantiated in the SoC until the PHY
has one owner.

---

## Draft 3 — the CSR/FIFO shim, with five requirements from the engine

`docs/usb-host-options.md` §15 has the register map. §23 has what the engine
demands of whatever drives it, and the shim is where four of the five live:

1. synthesise the completion edge from `status.idle` and hold it level-high for
   the PLIC, which has no edge detector;
2. latch `response` at that edge — it holds only until the next `start`;
3. count RX bytes drained from the FIFO; `rx_len` is 8 bits and wraps on a
   512-byte high-speed packet (asserted in `scripts/usb_host_sie_sim.py`);
4. raise a bit when `start` is issued while busy, because the engine ignores it
   silently;
5. cross `sync`/`usb` with the idioms already in the tree — the four-phase
   handshake at `ulpi_window.py:235-311` for the registers, `StreamBuffer` over
   `AsyncFIFOBuffered` for the byte streams.

The shim must not assume a scheduling model. Nothing above needs bounded latency
to be *correct*; latency costs throughput only, which is a measurement rather
than an argument.

---

## Draft 4 — firmware enumeration, and the milestone

Five control transfers, in Rust, over the shim: GET_DESCRIPTOR, SET_ADDRESS,
GET_DESCRIPTOR at the new address, SET_CONFIGURATION, then a bulk IN. The
sequence is already executed in `scripts/usb_host_sie_sim.py`'s testbench, so the
firmware has a reference that runs. Milestone: print a device descriptor.
