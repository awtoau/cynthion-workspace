#!/usr/bin/env python3
#
# The target-port test table (usb_target_cases.py) run against LUNA device models.
# SPDX-License-Identifier: BSD-3-Clause

"""
Sim backend for the target-port harness; see docs/target-test-harness.md.

    ./scripts/soc/usb_target_sim.py                   # every group, one process each
    ./scripts/soc/usb_target_sim.py control bulk      # some groups
    ./scripts/soc/usb_target_sim.py --list            # groups and case counts
    ./scripts/soc/usb_target_sim.py fuzz --seed 7 --fuzz 30
    ./scripts/soc/usb_target_sim.py bulk --only bulk_echo_64 -v

Groups = (device profile, case groups):
- control: model device (HS), control + address cases.
- bulk: bulk, interrupt, data toggle.   handshake: NAK/STALL/timeout/CRC/PID, wire faults.
- events: resets, role cycle, detach/attach, speed, unsupported features.
- events_fs: the FS-only model: speed, repeated resets (PHY regression).
- aoa: AoaPhone model: 51 / 52 / 53, re-enumeration, bulk echo.
- fuzz: seeded random cases; a failure prints its seed and a replay line.

Bench = shim + UlpiRegisters + PHY model + device behind a `WireFault` wire (usb_host_shim_sim.Bench).
"""

import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import _paths  # noqa: F401,E402

import usb_host_shim_sim as shim  # noqa: E402  (sets up sys.path for gateware)
import usb_target_cases as tc  # noqa: E402
from usb_target_cases import Backend, Result, NONE, DEFAULT, ADDRESSED, CONFIGURED  # noqa: E402
from devlog import emit  # noqa: E402
from sim_check_harness import Checks  # noqa: E402
from usb_host.model import (  # noqa: E402
    WireFault, scale_device_hs_fallback_for_simulation, scale_timing_for_simulation)
from usb_host.target_device import TargetDevice  # noqa: E402
from usb_host.aoa_phone import AoaPhone, PID_AOA, PID_AOA_ADB  # noqa: E402
from usb_host.own_device import OwnDevice  # noqa: E402
import usb_own_device as own  # noqa: E402
from peripherals.usb_host import (  # noqa: E402
    REG_CTRL, REG_ADDR, REG_EP, REG_EVENTS, REG_LINK, REG_RESP, REG_RXLEN_LO, REG_RXLEN_HI,
    REG_RX_TAG, REG_RX_DATA, REG_TX_DATA, REG_RXPID, CTRL_BUS_RESET, CTRL_START, EV_DONE,
    EV_BUSY_DROP, EV_TIMEOUT, EV_TX_OVERFLOW, ROLE_HOST, ROLE_REGS, ROLE_OFF, ROLE_RAW)

SPEEDS = ("HS", "FS", "LS", "?")
TY_SETUP, TY_IN, TY_OUT = 0, 1, 2
RESP_ACK, RESP_NAK, RESP_STALL, RESP_TIMEOUT, RESP_CRC, RESP_OVERFLOW = 1, 2, 3, 4, 5, 6
DATA_PID = (0xC3, 0x4B)     # DATA0, DATA1 as RXPID reads them

# Poll budgets: each is an upper bound with a known expected duration (reads are 2 sync ticks).
DONE_POLLS = 800            # one transaction < 12k usb cycles (shim_sim: DONE_POLLS)
OWN_CASE_STALL_CYCLES = 425_000   # own engine: longest case measured 338_429 usb cycles (own_ctrl_std); x1.26
CASE_STALL_CYCLES = 250_000   # longest case measured 182_419 usb cycles (reset_x3, #592); x1.37; sim time, not wall, so load cannot trip it
RX_SPINS_PER_BYTE = 8       # RX_TAG polls per byte while the 128-entry FIFO refills from the SIE
RX_DRAIN_MAX = 640          # firmware RX_DRAIN_MAX: SIE FIFO 512 + shim RX 128
NAK_TRIES = 40              # stands for the driver's 100 ms NAK window; a NAK is ~ a transaction
ERROR_TRIES = 3             # firmware TRIES: timeouts / CRC errors per stage
LINE_POLLS = 400            # LINK.connected follows a detach/attach within the PHY debounce
GAP_AFTER_START = 3000      # usb cycles the phone needs to re-appear (shim_sim group_aoa)
ATTEMPTS = 8                # aoa re-attach loop, as the driver


def set_fault(fault, ctx, mode):
    ctx.set(fault.drop, mode == "drop")
    ctx.set(fault.xor, {"crc": 0x01, "pid": 0x10}.get(mode, 0))
    ctx.set(fault.index, 1 if mode == "crc" else 0)


class SimBackend(Backend):
    name = "sim"

    own = False

    def __init__(self, ctx, bench, pc, fault, prof, phy_writes, clock):
        self.ctx, self.bench, self.pc, self.fault, self.prof = ctx, bench, pc, fault, prof
        self.clock = clock
        self.phy_writes = phy_writes
        self.in_quirk = False
        self.level = NONE
        self.forget()

    def case_clock(self):
        return self.clock[0]

    def case_stalled(self, started):
        return self.clock[0] - started > (OWN_CASE_STALL_CYCLES if self.own else CASE_STALL_CYCLES)

    def forget(self):
        self.addr, self.mps0, self.tog_in, self.tog_out = 0, 8, 0, 0

    async def op(self, _op, **a):
        return await getattr(self, f"do_{_op}")(**a)

    # ---- one transaction and the driver's retry loop (usb_host.rs: transact / stage)
    async def transact(self, ty, ep, pid, out=b""):
        pc = self.pc
        for byte in out:
            await pc.write(REG_TX_DATA, byte)
        await pc.write(REG_ADDR, self.addr)
        await pc.write(REG_EP, ep | ty << 4 | pid << 6)
        await pc.write(REG_EVENTS, 0xFF)
        await pc.write(REG_CTRL, CTRL_START)
        events = await shim.poll(pc, REG_EVENTS, lambda v: v & EV_DONE, DONE_POLLS, gap=8)
        if events is None:
            return "hang", 0, b""
        if events & (EV_BUSY_DROP | EV_TIMEOUT | EV_TX_OVERFLOW):
            if ty == TY_IN:
                await self.discard_rx(True)
            return "dropped", 0, b""
        resp = await pc.read(REG_RESP) & 7
        if resp == RESP_ACK and ty == TY_IN and await pc.read(REG_RXPID) != DATA_PID[pid]:
            resp = "resend"
        length = await pc.read(REG_RXLEN_LO) | (await pc.read(REG_RXLEN_HI) << 8)
        data, spins = [], 0
        if resp not in (RESP_ACK, "resend"):
            if ty == TY_IN:                     # usb_host.rs discard_rx
                await self.discard_rx(resp not in (RESP_NAK, RESP_STALL))
            return resp, length, b""
        while len(data) < length and spins < length * RX_SPINS_PER_BYTE + 64:
            spins += 1
            if await pc.read(REG_RX_TAG) & 2:
                data.append(await pc.read(REG_RX_DATA))
        return resp, length, bytes(data)

    async def discard_rx(self, settle):
        """Pop until empty; `settle` gives each straggler RX_SPINS_PER_BYTE polls (firmware FIFO_US)."""
        for _ in range(RX_DRAIN_MAX):
            spins = RX_SPINS_PER_BYTE if settle else 1
            while spins and not await self.pc.read(REG_RX_TAG) & 2:
                spins -= 1
            if not spins:
                return
            await self.pc.read(REG_RX_DATA)

    async def stage(self, ty, ep, pid, out=b"", want=None):
        """Returns (kind, data). `want` caps returned bytes like the firmware's buffer slice."""
        errors = 0
        for _ in range(NAK_TRIES):
            resp, length, data = await self.transact(ty, ep, pid, out)
            if resp in ("hang", "dropped"):
                return resp, b""
            if resp == "resend":            # wrong DATA PID: ACKed by the SIE, dropped (8.6.4)
                continue
            if resp == RESP_ACK:
                return "ack", data[:want] if want is not None else data
            if resp == RESP_NAK:
                continue
            if resp == RESP_STALL:
                return "stall", b""
            errors += 1
            if errors >= ERROR_TRIES:
                return {RESP_TIMEOUT: "timeout", RESP_CRC: "crc"}.get(resp, "overflow"), b""
        return "nak", b""

    # ---- ops
    async def do_reset(self):
        pc = self.pc
        await pc.write(REG_CTRL, CTRL_BUS_RESET)
        if await shim.poll(pc, REG_LINK, lambda v: not v & 0x80, shim.RESET_CLEAR_POLLS) is None:
            return Result("noreset", text="reset_done did not clear")
        if await shim.poll(pc, REG_LINK, lambda v: v & 0x80, shim.BRINGUP_POLLS, gap=64) is None:
            self.level = NONE
            return Result("noreset", text="reset_done did not return")
        self.forget()
        self.level = DEFAULT
        return Result("ack")

    async def do_role(self, name):
        role = {"off": ROLE_OFF, "regs": ROLE_REGS, "host": ROLE_HOST, "raw": ROLE_RAW}[name]
        status = await shim.set_role(self.pc, role, polls=400)
        self.forget()
        self.level = NONE
        if shim.role_of(status) != role:
            return Result("error", text=f"role status {status}")
        if role == ROLE_HOST and await shim.poll(
                self.pc, REG_LINK, lambda v: v & 0x80, shim.BRINGUP_POLLS, gap=64) is None:
            return Result("noreset", text="no reset_done after entering host")
        return Result("ack")

    async def do_link(self):
        v = await self.pc.read(REG_LINK)
        return Result("ack", info={"connected": bool(v & 0x10), "speed": SPEEDS[(v >> 2) & 3],
                                   "reset_done": bool(v & 0x80), "ls": v & 3})

    async def do_setaddr(self, addr):
        self.addr = addr & 0x7F
        return Result("ack")

    async def do_setup(self, data):
        kind, _ = await self.stage(TY_SETUP, 0, 0, data)
        return Result(kind)

    async def do_ctrl(self, bm, req, val, idx, len, out=b""):
        """usb_host.rs `Host::control`, step for step."""
        setup = tc.sp(bm, req, val, idx, len)
        kind, _ = await self.stage(TY_SETUP, 0, 0, setup)
        if kind != "ack":
            return Result(kind)
        mps, toggle, n, buf = self.mps0, 1, 0, bytearray()
        want = min(len, tc.CTRL_BUF)
        if bm & 0x80 and len != 0:
            while n < want:
                kind, got = await self.stage(TY_IN, 0, toggle, want=want - n)
                if kind != "ack":
                    return Result(kind, bytes(buf))
                buf += got
                n += len_(got)
                toggle ^= 1
                if len_(got) < mps:
                    break
            kind, _ = await self.stage(TY_OUT, 0, 1)
        else:
            for off in range(0, len_(out), mps):
                kind, _ = await self.stage(TY_OUT, 0, toggle, out[off:off + mps])
                if kind != "ack":
                    return Result(kind)
                toggle ^= 1
            kind, _ = await self.stage(TY_IN, 0, 1)
        if kind == "ack" and bm == 0 and req == 9:
            self.tog_in = self.tog_out = 0
            if self.prof.bulk_ep and not self.in_quirk:
                # LUNA only restarts toggles on CLEAR_FEATURE(ENDPOINT_HALT), not SET_CONFIGURATION.
                self.in_quirk = True
                for ep in (0x01, 0x81, 0x82):
                    await self.do_ctrl(0x02, 1, 0, ep, 0)
                self.in_quirk = False
        if kind == "ack" and bm == 0x02 and req == 1 and val == 0:
            bit = 1 << (idx & 15)
            if idx & 0x80:
                self.tog_in &= ~bit
            else:
                self.tog_out &= ~bit
        return Result(kind, bytes(buf))

    async def do_enumerate(self, addr):
        r = await self.do_ctrl(0x80, 6, 0x0100, 0, 8)
        if r.kind != "ack" or len_(r.data) < 8 or r.data[7] not in (8, 16, 32, 64):
            return Result(r.kind if r.kind != "ack" else "error", text=f"head {r.data.hex()}")
        self.mps0 = r.data[7]
        r = await self.do_ctrl(0, 5, addr, 0, 0)
        if r.kind != "ack":
            return r
        self.addr = addr
        r = await self.do_ctrl(0x80, 6, 0x0100, 0, 18)
        if r.kind != "ack" or len_(r.data) != 18:
            return Result(r.kind if r.kind != "ack" else "error", text=f"desc {r.data.hex()}")
        self.level = ADDRESSED
        return Result("ack", r.data[8:12])

    async def do_bulk_in(self, ep, n, toggle=None):
        bit = 1 << (ep & 15)
        pid = (self.tog_in & bit != 0) if toggle is None else toggle
        kind, data = await self.stage(TY_IN, ep & 15, int(pid), want=n)
        if kind == "ack":
            self.tog_in ^= bit
        return Result(kind, data)

    async def do_bulk_out(self, ep, data, toggle=None):
        bit = 1 << (ep & 15)
        pid = (self.tog_out & bit != 0) if toggle is None else toggle
        kind, _ = await self.stage(TY_OUT, ep & 15, int(pid), data)
        if kind == "ack":
            self.tog_out ^= bit
        return Result(kind)

    async def do_fault(self, mode):
        set_fault(self.fault, self.ctx, mode)
        return Result("ack")

    async def do_detach(self):
        self.ctx.set(self.bench.device.present, 0)
        return await self.await_link(False)

    async def do_attach(self):
        self.ctx.set(self.bench.device.present, 1)
        return await self.await_link(True)

    async def do_vbus(self, state):      # no VBUS model: loss of VBUS is a detach
        self.level = NONE
        return await (self.do_detach() if state == "off" else self.do_attach())

    async def do_await_connect(self):
        return await self.await_link(True)

    async def await_link(self, connected):
        v = await shim.poll(self.pc, REG_LINK, lambda v: bool(v & 0x10) == connected, LINE_POLLS, gap=4)
        return Result("ack" if v is not None else "hang", text=f"connected never {connected}")

    async def do_reset_during_in(self, ep):
        """START an IN that will NAK, then BUS_RESET before it can finish."""
        pc = self.pc
        await pc.write(REG_ADDR, self.addr)
        await pc.write(REG_EP, ep | TY_IN << 4)
        await pc.write(REG_EVENTS, 0xFF)
        await pc.write(REG_CTRL, CTRL_START)
        await pc.write(REG_CTRL, CTRL_BUS_RESET)
        self.forget()
        self.level = NONE
        if await shim.poll(pc, REG_LINK, lambda v: not v & 0x80, shim.RESET_CLEAR_POLLS) is None:
            return Result("noreset", text="reset_done did not clear")
        up = await shim.poll(pc, REG_LINK, lambda v: v & 0x80 and v & 0x40, shim.BRINGUP_POLLS, gap=64)
        return Result("ack" if up is not None else "noreset")

    async def do_clear_phy_writes(self):
        self.phy_writes.clear()
        return Result("ack")

    async def do_phy_writes(self):
        return Result("ack", info={"writes": list(self.phy_writes)})

    async def do_recover(self):
        """Fuzz oracle: the device still answers GET_DESCRIPTOR at its address, no reset needed."""
        self.addr = tc.DEV_ADDR
        return await self.do_ctrl(0x80, 6, 0x0100, 0, 18)

    async def do_aoa_full(self):
        """Driver order: 51, 52 x6, 53, reset until 18d1:2d0x, configure, bulk echo."""
        r = await self.do_ctrl(0xC0, 51, 0, 0, 2)
        if r.kind != "ack" or r.data[:1] == b"\0":
            return Result("error", text=f"protocol {r.data.hex()}")
        for index, text in enumerate(shim.AOA_STRINGS):
            r = await self.do_ctrl(0x40, 52, 0, index, len_(text) + 1, text.encode() + b"\0")
            if r.kind != "ack":
                return r
        await self.do_ctrl(0x40, 53, 0, 0, 0)          # the phone detaches during START
        accessory = None
        for _ in range(ATTEMPTS):
            await self.pc.idle(GAP_AFTER_START)
            if (await self.do_reset()).kind != "ack":
                continue
            r = await self.do_enumerate(tc.DEV_ADDR)
            if r.kind == "ack" and (r.data[2] | r.data[3] << 8) in (PID_AOA, PID_AOA_ADB):
                accessory = r
                break
        if accessory is None:
            return Result("hang", info={"accessory": False}, text="never re-attached as accessory")
        await self.do_ctrl(0, 9, 1, 0, 0)
        payload = bytes(range(0x30, 0x30 + 31))
        out = await self.do_bulk_out(1, payload)
        got = await self.do_bulk_in(1, 64)
        ok = out.kind == "ack" and got.data == payload
        self.level = CONFIGURED
        return Result("ack" if ok else "error", info={"accessory": True},
                      text=f"bulk out {out.kind} in {got.kind} {got.data.hex()}")


def len_(x):
    return len(x)


# ------------------------------------------------------------------------- groups

# name: (device profile, case groups, resets_after_bring_up). Resets cost ~12 s of simulation
# each (HS fallback scaling), so groups that never reset keep the cheap short reset.
GROUPS = {
    "ctrl_desc": ("model", ("ctrl_desc",), False),
    "ctrl_string": ("model", ("ctrl_string",), False),
    "ctrl_std": ("model", ("ctrl_std",), False),
    "address": ("model", ("address",), False),
    "bulk": ("model", ("bulk",), False),
    "bulk_int": ("model", ("bulk_int",), True),
    "handshake": ("model", ("handshake",), True),
    "events": ("model", ("events",), True),
    "events_regress": ("model", ("events_regress",), True),
    "events_misc": ("model", ("events_misc",), True),
    "events_fs": ("model_fs", ("events", "events_regress", "events_detach"), False),
    "aoa": ("aoa_phone", ("aoa",), False),
    "aoa_seq": ("aoa_phone", ("aoa_seq",), True),
    "fuzz": ("model", ("fuzz",), True),
    # Our own device engine (usbcore.device) in place of the LUNA model, firmware stand-in on its CPU port.
    "own_ctrl_desc": ("model", ("ctrl_desc",), False),
    "own_ctrl_string": ("model", ("ctrl_string",), False),
    "own_ctrl_std": ("model", ("ctrl_std",), False),
    "own_address": ("model", ("address",), False),
    # Increment 4: bulk EP1-4 behind the engine (echo, interrupt counter, pattern source, throttled echo).
    "own_bulk": ("model", ("bulk",), False),
    "own_bulk_x": ("model", ("own_bulk_x",), False),
    # Increment 2: the same cases with every SETUP relayed over a modelled AUX control channel to a stub.
    "own_relay_std": ("model", ("ctrl_std",), False),
    "own_relay_address": ("model", ("address",), False),
}

OWN_NOT_APPLICABLE = {      # LUNA-model behaviour the case pins; own_bulk_x covers our behaviour instead
    "bulk_zlp_out": "the model drops a ZLP OUT; our echo returns it as a ZLP IN (own_zlp_echo)",
}
RELAY_LINK_CYCLES = 100     # one way, usb cycles: stands for one AUX bulk transfer (~100 us at HS polling)


def make_firmware(name, dev):
    if name.startswith("own_relay"):
        return own.RelayFirmware(dev.engine, own.Stub(), link=RELAY_LINK_CYCLES)
    return own.TableFirmware(dev.engine, latency=40, sabotage=SABOTAGE)


SABOTAGE = False        # --sabotage: the own firmware serves a wrong byte; the own_* groups must FAIL


def make_bench(prof, own_device=False):
    fault = WireFault()
    if own_device:
        dev = OwnDevice()
    elif prof.name == "aoa_phone":
        dev = AoaPhone(detach_cycles=1500)
    else:
        dev = TargetDevice(full_speed_only=prof.name == "model_fs")
    return shim.Bench(phone=dev, fault=fault, report_disconnect=True, switch_limit=3000), fault, dev


def run_group(name, args):
    profile, case_groups, resets = GROUPS[name]
    own_device = name.startswith("own_")
    if resets:
        scale_device_hs_fallback_for_simulation()
    prof = tc.get_profile(profile)
    cases = tc.table()
    for c in cases:                         # unsupported cases are listed once, under the model
        if c.sim:
            c.profiles = ("model",)
    if name == "fuzz":
        cases = tc.fuzz_cases(args.seed, args.fuzz, prof)
    cases = tc.select(cases, prof, args.only, case_groups)
    if own_device:
        for c in [c for c in cases if c.name in OWN_NOT_APPLICABLE]:
            emit(f"  NOT APPLICABLE  {name}: {c.name}: {OWN_NOT_APPLICABLE[c.name]}")
        cases = [c for c in cases if c.name not in OWN_NOT_APPLICABLE]
    started_group = time.monotonic()
    bench, fault, dev = make_bench(prof, own_device)
    firmware = make_firmware(name, dev) if own_device else None
    results, phy_writes = [], []
    worst = [0]
    clock = [0]                             # usb cycles since reset: the case guard's time base

    async def watch_phy(ctx):
        last = int(ctx.get(bench.phy.func_ctrl))
        while True:
            await ctx.tick("usb")
            clock[0] += 1
            now = int(ctx.get(bench.phy.func_ctrl))
            if now != last:
                phy_writes.append(now)
                last = now

    async def testbench(ctx):
        pc = shim.Csr(ctx, bench.port.bus)
        backend = SimBackend(ctx, bench, pc, fault, prof, phy_writes, clock)
        backend.own = own_device
        up = await shim.host_up(pc)
        if up is None:
            results.append((None, "FAIL", "host never reached reset_done"))
            return
        backend.level = DEFAULT
        for case in cases:
            started = time.monotonic()
            c0 = clock[0]
            status, detail = await tc.run_case(backend, case, prof, emit)
            results.append((case, status, detail, time.monotonic() - started))
            worst[0] = max(worst[0], clock[0] - c0)
            if status in ("FAIL", "XFAIL"):
                set_fault(fault, ctx, "none")
                ctx.set(dev.present, 1) if prof.name != "aoa_phone" else None
                backend.invalidate()

    extra = (watch_phy,) + ((firmware.run,) if firmware else ()) \
        + ((firmware.data_run,) if firmware and hasattr(firmware, 'data_run') else ())
    shim.run(bench, testbench, extra=extra, platform=shim.BoardPlatform())
    if firmware:
        emit(f"  {name}: own engine answered {len(getattr(firmware, 'log', None) or firmware.messages) } SETUPs/messages")
    checks = Checks(emit)
    emit(f"  {name}: profile {profile}, {len(cases)} cases, {time.monotonic() - started_group:.0f} s, "
         f"longest case {worst[0]} usb cycles")
    if not results:
        results.append((None, "FAIL", "testbench produced no results (hang or empty selection)"))
    for item in results:
        case, status, detail = item[0], item[1], item[2]
        label = f"{name}: {case.name}" if case else f"{name}: bring-up"
        if status == "PASS":
            checks.check(label, True)
        elif status == "FAIL":
            replay = f" [replay: {Path(__file__).name} fuzz --seed {args.seed} --fuzz {args.fuzz} " \
                     f"--only {case.name}]" if case is not None and case.group == "fuzz" else ""
            checks.check(label, False, detail + replay)
        elif status == "XFAIL":
            emit(f"  XFAIL        {label}: {detail}")
        elif status == "UNSUPPORTED":
            emit(f"  UNSUPPORTED  {label}: {detail}")
    return checks.summary()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("groups", nargs="*", help="only these groups")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--seed", type=int, default=1, help="fuzz seed; a failure prints it")
    parser.add_argument("--fuzz", type=int, default=8, help="fuzz cases in group `fuzz`")
    parser.add_argument("--only", nargs="*", default=[], help="substrings of case names")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--sabotage", action="store_true",
                        help="own_* groups: serve a wrong descriptor byte (negative control: must FAIL)")
    args = parser.parse_args()
    global SABOTAGE
    SABOTAGE = args.sabotage
    scale_timing_for_simulation()
    chosen = [g for g in GROUPS if not args.groups or g in args.groups]
    if args.list:
        cases = tc.table()
        for g in chosen:
            prof, cgs, _ = GROUPS[g]
            n = sum(1 for c in cases if c.group in cgs and prof in c.profiles and not c.sim)
            emit(f"  {g:10} profile {prof:10} {n} supported cases ({', '.join(cgs)})")
        emit(f"  unsupported in sim: {sum(1 for c in cases if c.sim)}")
        return 0
    emit("target-port harness, sim backend (scripts/soc/usb_target_cases.py)")
    if len(chosen) == 1:
        return run_group(chosen[0], args)
    # Amaranth's builder is not thread-safe: one process per group (as usb_host_shim_sim).
    extra = ["--seed", str(args.seed), "--fuzz", str(args.fuzz)] + (["--only", *args.only] if args.only else []) \
        + (["--sabotage"] if args.sabotage else [])
    procs = [(g, subprocess.Popen([sys.executable, __file__, g, *extra], stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)) for g in chosen]
    status = 0
    for g, proc in procs:
        out, _ = proc.communicate()
        for line in out.splitlines()[1:]:
            emit(line)
        status |= proc.returncode
    return status


if __name__ == "__main__":
    sys.exit(main())
