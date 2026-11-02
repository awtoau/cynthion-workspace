#!/usr/bin/env python3
#
# HyperRAM CR0/CR1 init sequencer against a register-aware model. See #319.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks `HyperRAMInit` (gateware/soc/peripherals/hyperram_init.py):

  * CR0/CR1 write bytes on the wire equal datasheet Table 5 CA + data, per parameter set
  * CS# high between every transaction >= tCSHI at the controller clock
  * `start_transfer` is a single cycle per register write
  * nothing reaches the controller before `ready`
  * read-back mismatch -> `error`, `ready` low, user `start_transfer` stays off
  * NEGATIVE: a model that drops the CR0 write makes the landed-value check fail

    ./scripts/soc/soc_hyperram_init_sim.py
"""

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from devlog import emit  # noqa: E402
from amaranth import Elaboratable, Module  # noqa: E402
from amaranth.hdl import Fragment  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

from peripherals.hyperram_controller import HyperRAMController, T_CSHI_NS  # noqa: E402
from peripherals.hyperram_init import HyperRAMInit  # noqa: E402
from luna.gateware.interface.psram import HyperBusPHY  # noqa: E402
from sim_check_harness import Checks  # noqa: E402
from soc_hyperram_sim import ModelHyperRAM16  # noqa: E402

# Datasheet Table 5 (register CA): 0x60 = write | register space | linear. Address in
# the CA is the word address >> 3 at CA[44:16]; CR0 0x0800, CR1 0x0801.
CA_CR0 = bytes([0x60, 0x00, 0x01, 0x00, 0x00, 0x00])
CA_CR1 = bytes([0x60, 0x00, 0x01, 0x00, 0x00, 0x01])

# (name, kwargs, expected CR0 bytes, expected CR1 bytes), written out by hand from
# the field tables in docs/chips/hyperram/w956a8.md, not computed by the module.
CASES = [
    ("defaults", {}, bytes([0x8f, 0x2f]), bytes([0xff, 0xc1])),
    ("6 clk, variable, drive 111, differential",
     dict(latency_clocks=6, fixed_latency=False, drive=0b111,
          differential_clock=True),
     bytes([0xff, 0x17]), bytes([0xff, 0x81])),
    ("3 clk fixed, drive 001",
     dict(latency_clocks=3, drive=0b001), bytes([0x9f, 0xef]), bytes([0xff, 0xc1])),
]


class RegisterModel(ModelHyperRAM16):
    """16-bit model that also stores/serves registers and logs register writes."""

    def __init__(self, *, drop_cr0=False, **kw):
        super().__init__(**kw)
        self.regs = {0x0800: 0x8f2f, 0x0801: 0xffc1}
        self.drop_cr0 = drop_cr0
        self.writes = []        # (CA bytes, data bytes)
        self._reg = False
        self._seen = 0
        self._wdata = []

    def step(self, *, cs, clk_en, dq_o, dq_e):
        pre = self._state
        if pre == "data" and self._reg and clk_en:
            if self._is_write and dq_e:
                self._wdata += [dq_o >> 8, dq_o & 0xff]
                if len(self._wdata) == 2:
                    ca = self.ca_words[-1].to_bytes(6, "big")
                    self.writes.append((ca, bytes(self._wdata)))
                    if not (self.drop_cr0 and self._address == 0x0800):
                        self.regs[self._address] = (self._wdata[0] << 8) | self._wdata[1]
        dq_i, rwds_i = super().step(cs=cs, clk_en=clk_en, dq_o=dq_o, dq_e=dq_e)
        if len(self.ca_words) != self._seen:
            self._seen = len(self.ca_words)
            self._reg = bool((self.ca_words[-1] >> 46) & 1)
            self._wdata = []
            if self._reg and self._is_write:
                self._state = "data"        # register writes have no latency
        if pre == "data" and self._reg and not self._is_write and clk_en:
            dq_i = self.regs.get(self.commands[-1], 0)
        return dq_i, rwds_i


class Rig(Elaboratable):
    def __init__(self, sync_mhz, **init):
        self.sync_mhz = sync_mhz
        self.phy = HyperBusPHY()
        self.psram = HyperRAMController(phy=self.phy, sync_mhz=sync_mhz,
                                        phy_round_trip_cycles=0)
        self.init = HyperRAMInit(ctrl=self.psram, phy=self.phy,
                                 sync_mhz=sync_mhz, **init)

    def elaborate(self, platform):
        m = Module()
        m.submodules.psram = self.psram
        m.submodules.init = self.init
        return m


def run(sync_mhz, *, model, init=None, hold_user=False, extra=0):
    """Run to `ready`/`error` (+ `extra` cycles). Returns a result dict."""
    dut = Rig(sync_mhz, **(init or {}))
    i = dut.init
    # Bound: 1.25 x (reset + power-up waits + 4 register transactions at txn_cycles).
    bound = math.ceil(1.25 * (i.reset_cycles + i.powerup_cycles + 4 * i.txn_cycles)) + extra
    out = dict(cycles=0, starts=[], early_starts=0, ready=0, error=0, mismatch=0,
               user_leaked=0, reset_cycles=0, i=i)

    async def tb(ctx):
        if hold_user:                       # a caller that never lets go of start
            ctx.set(i.req_start_transfer, 1)
            ctx.set(i.req_register_space, 0)
        prev_start = 0
        for n in range(bound):
            dq_i, rwds_i = model.step(
                cs=ctx.get(dut.phy.cs), clk_en=ctx.get(dut.phy.clk_en),
                dq_o=ctx.get(dut.phy.dq.o), dq_e=ctx.get(dut.phy.dq.e))
            ctx.set(dut.phy.dq.i, dq_i)
            ctx.set(dut.phy.rwds.i, rwds_i)
            start = ctx.get(dut.psram.start_transfer)
            ready = ctx.get(i.ready)
            out["reset_cycles"] += ctx.get(dut.phy.reset)
            if start:
                out["starts"].append(n)
                if not ready and not ctx.get(dut.psram.register_space):
                    out["user_leaked"] += 1
            if start and prev_start:
                out["back_to_back"] = True
            prev_start = start
            out["error"] = ctx.get(i.error)
            out["mismatch"] = ctx.get(i.mismatch)
            out["ready"] = ready
            out["cycles"] = n
            if (ready or out["error"]) and n > bound - extra:
                break
            if (ready or out["error"]) and not extra:
                break
            await ctx.tick()
        out["cr0_read"] = ctx.get(i.cr0_read)
        out["cr1_read"] = ctx.get(i.cr1_read)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / (sync_mhz * 1e6), domain="sync")
    sim.add_testbench(tb)
    sim.run()
    return out


def section_sequences(checks):
    for sync_mhz in (60.0, 100.0, 165.0):
        for name, kw, cr0, cr1 in CASES:
            tag = f"{name} @ {sync_mhz:g} MHz"
            model = RegisterModel(sync_mhz=sync_mhz)
            r = run(sync_mhz, model=model, init=kw)
            w = model.writes
            checks.check(f"{tag}: two register writes, CR0 then CR1",
                         [x[0] for x in w] == [CA_CR0, CA_CR1], detail=str(w))
            checks.check(f"{tag}: CR0 data bytes {cr0.hex()}",
                         len(w) > 0 and w[0][1] == cr0, detail=str(w))
            checks.check(f"{tag}: CR1 data bytes {cr1.hex()}",
                         len(w) > 1 and w[1][1] == cr1, detail=str(w))
            checks.check(f"{tag}: CR0[11:8] reserved = 1111",
                         len(w) > 0 and (w[0][1][0] & 0x0f) == 0x0f)
            checks.check(f"{tag}: read-back verified, ready, no error",
                         r["ready"] == 1 and r["error"] == 0,
                         detail=f"ready={r['ready']} error={r['error']}")
            checks.check(f"{tag}: 4 single-cycle start_transfers, none adjacent",
                         len(r["starts"]) == 4 and "back_to_back" not in r,
                         detail=str(r["starts"]))
            need = math.ceil(T_CSHI_NS * sync_mhz / 1000.0)
            gap = model.cs_high_min
            checks.check(f"{tag}: CS# high >= tCSHI {T_CSHI_NS:g} ns",
                         gap is not None and gap * 1000.0 / sync_mhz >= T_CSHI_NS,
                         detail=f"gap {gap} cycles",
                         measurement=f"min CS# high {gap} cycles = "
                                     f"{(gap or 0) * 1000.0 / sync_mhz:.2f} ns "
                                     f"(need {need} cycles)")


def section_power_up(checks):
    sync_mhz = 100.0
    model = RegisterModel(sync_mhz=sync_mhz)
    r = run(sync_mhz, model=model, hold_user=True)
    i = r["i"]
    first = r["starts"][0] if r["starts"] else 0
    checks.check("RESET# held for tRP before anything else",
                 r["reset_cycles"] == i.reset_cycles,
                 detail=f"{r['reset_cycles']} vs {i.reset_cycles}")
    checks.check("first start_transfer only after reset + power-up wait",
                 first >= i.reset_cycles + i.powerup_cycles,
                 detail=f"first {first}, wait {i.reset_cycles + i.powerup_cycles}")
    checks.check("a user that holds start_transfer high cannot get through "
                 "before ready", r["user_leaked"] == 0)


def section_faults(checks):
    sync_mhz = 100.0
    # Part ignores the CR0 write: read-back returns the default, which differs from
    # the requested CR0, so the sequencer must flag it and keep traffic off.
    kw = dict(latency_clocks=6)
    model = RegisterModel(sync_mhz=sync_mhz, drop_cr0=True)
    r = run(sync_mhz, model=model, init=kw, hold_user=True, extra=200)
    checks.check("dropped CR0 write: error + mismatch raised",
                 r["error"] == 1 and r["mismatch"] == 1)
    checks.check("dropped CR0 write: ready stays low", r["ready"] == 0)
    checks.check("dropped CR0 write: user start_transfer never reaches controller",
                 r["user_leaked"] == 0 and len(r["starts"]) == 4,
                 detail=str(r["starts"]))
    checks.check("dropped CR0 write: read-back is the default 0x8f2f",
                 r["cr0_read"] == 0x8f2f, detail=hex(r["cr0_read"]))

    # NEGATIVE control of the landed-value check itself: against the dropping
    # model "the register holds what we wrote" is false, so a test relying on it
    # fails when the write is dropped.
    landed = model.regs[0x0800] == r["i"].cr0
    checks.check("negative: landed-value check FAILS when the write is dropped",
                 landed is False)
    good = RegisterModel(sync_mhz=sync_mhz)
    run(sync_mhz, model=good, init=kw)
    checks.check("control: same check PASSES when the write lands",
                 good.regs[0x0800] == r["i"].cr0)

    # Part that never returns a register word: bounded by the watchdog, not a hang.
    dead = RegisterModel(sync_mhz=sync_mhz, deliver=0)
    r = run(sync_mhz, model=dead, hold_user=True, extra=50)
    checks.check("silent part: timeout error, ready low",
                 r["error"] == 1 and r["ready"] == 0)


def main():
    emit("HyperRAM init sequencer (#319), against a register-aware model")
    checks = Checks(emit)
    section_sequences(checks)
    section_power_up(checks)
    section_faults(checks)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
