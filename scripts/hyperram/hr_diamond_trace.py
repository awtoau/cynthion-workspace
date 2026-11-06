#!/usr/bin/env python3
"""Lattice TRACE on the SoC HyperRAM peripheral, standalone (#628). Analysis only.

- Harness: `HyperRAM` (PLL, PHY, engine, CSR, window) on the r1.4 HyperRAM pins; every
  peripheral input from one shift chain (pin E13), every output XOR-folded to pin C13.
  `usb` = the 60 MHz pin A8 (as in the SoC), `sync` = a second clock pin (B14, an LED site: unrelated).
- Flow: Amaranth RTLIL -> yosys `proc` -> Verilog -> Diamond LSE, map, par, trce.
- Device LFE5U-25F (the die; Diamond enforces the 12F marking). NEVER program its bitstream:
  bitgen is not run.
- Clocks: FREQUENCY PORT only; TRACE derives hr / hr90 (+90 degrees) from the PLL.

    ./scripts/hyperram/hr_diamond_trace.py --speed 6      # the board's grade
    ./scripts/hyperram/hr_diamond_trace.py --nextpnr --seed 1   # same harness, open flow

Outputs: tmp/hr628/diamond-s<speed>/ (vendor output, never committed). Log: tmp/logs/hr_diamond_trace.log.
"""

import argparse
import os
import re
import selectors
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
GW = ROOT / "gateware"
LOG = ROOT / "tmp" / "logs" / "hr_diamond_trace.log"
OSS_CAD_ENV = Path.home() / "opt" / "fpga-main" / "environment"

DIAMOND = Path.home() / "lscc" / "diamond" / "3.14"
FOUNDRY = DIAMOND / "ispfpga"
ARCH, DEVICE, PACKAGE = "ECP5U", "LFE5U-25F", "CABGA256"
# Dev-box rule (awto-dan#53): an engine silent for 60 s is hung; overall backstop per engine.
STALL_S = 60
# Backstops: 3x a cold run of each engine on this harness (LSE/map/par/trce) once measured;
# first-run values from the retired scripts/diamond/flow.py bounds (83700aa).
BACKSTOP_S = {"synthesis": 1800, "map": 1200, "par": 2400, "trce": 900, "yosys": 600,
              "nextpnr": 900}


def say(text):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {text}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def diamond_env():
    """Diamond's engines need their own libs first and no oss-cad PYTHONHOME."""
    env = dict(os.environ)
    for k in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(k, None)
    bindir, fpgabin = DIAMOND / "bin" / "lin64", FOUNDRY / "bin" / "lin64"
    env.update(LSC_DIAMOND="true", FOUNDRY=str(FOUNDRY), QT_PLUGIN_PATH="",
               NEOCAD_MAXLINEWIDTH="32767",
               TCL_LIBRARY=str(DIAMOND / "tcltk" / "lib" / "tcl8.5"),
               LM_LICENSE_FILE=str(DIAMOND / "license" / "license.dat"),
               PATH=f"{bindir}:{fpgabin}:{env.get('PATH', '')}",
               LD_LIBRARY_PATH=f"{bindir}:{fpgabin}")
    return env


def newest_mtime(path):
    return max((p.stat().st_mtime for p in Path(path).rglob("*") if p.is_file()), default=0)


def cpu_ticks(pid):
    """utime + stime of a process (0 once gone)."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[11]) + int(fields[12])
    except (OSError, IndexError, ValueError):
        return 0


def run(cmd, cwd, env, engine, logfile):
    """Run one engine with a stall guard and a backstop; raise on failure or expiry."""
    say(f"{engine}: {' '.join(map(str, cmd))}")
    start, last = time.monotonic(), ""
    with open(logfile, "w") as f:
        proc = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        while True:
            elapsed = time.monotonic() - start
            # Activity = stdout, a file in the work dir written, or the engine's CPU time
            # advancing (LSE optimises for minutes with neither); the backstop bounds the rest.
            ticks = cpu_ticks(proc.pid)
            ready = sel.select(timeout=STALL_S)
            if not ready and (time.time() - newest_mtime(cwd) < STALL_S
                              or cpu_ticks(proc.pid) > ticks):
                continue
            if elapsed > BACKSTOP_S[engine] or not ready:
                proc.kill()
                raise SystemExit(f"{engine}: killed after {elapsed:.0f} s (stall {STALL_S} s / "
                                 f"backstop {BACKSTOP_S[engine]} s); last line: {last[:200]}; "
                                 f"loadavg {os.getloadavg()}")
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            text = chunk.decode(errors="replace")
            f.write(text)
            last = text.strip().rsplit("\n", 1)[-1] or last
        proc.wait()
    say(f"{engine}: rc {proc.returncode} in {time.monotonic() - start:.0f} s -> {logfile}")
    if proc.returncode != 0:
        raise SystemExit(f"{engine} failed; last line: {last[:300]}")


# ---- harness ---------------------------------------------------------------------

ELABORATE = r"""
import sys
sys.path[:0] = [{gw!r} + "/hyperram_baseline", {gw!r} + "/soc"]
from amaranth import Cat, ClockDomain, ClockSignal, Elaboratable, Module, Signal
from amaranth.build import Attrs, Clock, Pins, Resource
from amaranth.lib import wiring
from board_r14 import CynthionR14Baseline
from peripherals.hyperram import HyperRAM


class Harness(Elaboratable):
    def elaborate(self, platform):
        m = Module()
        m.domains += [ClockDomain("usb"), ClockDomain("sync")]
        m.d.comb += [ClockSignal("usb").eq(platform.request("clk_60MHz").i),
                     ClockSignal("sync").eq(platform.request("clk_sync").i)]
        m.submodules.hyperram = hr = HyperRAM()
        si, so = platform.request("chain_in").i, platform.request("chain_out").o
        ins, outs = [], []
        for _path, member, value in hr.signature.flatten(hr):
            (ins if member.flow == wiring.In else outs).append(value)
        width = sum(len(v) for v in ins)
        chain = Signal(width)
        m.d.sync += chain.eq(Cat(si, chain[:-1]))
        pos = 0
        for v in ins:
            m.d.comb += v.eq(chain[pos:pos + len(v)])
            pos += len(v)
        fold = Signal()
        m.d.sync += fold.eq(Cat(*outs).xor())
        m.d.comb += so.eq(fold)
        return m


class Plat(CynthionR14Baseline):
    resources = CynthionR14Baseline.resources + [
        Resource("clk_sync", 0, Pins("B14", dir="i"), Clock(60e6), Attrs(IO_TYPE="LVCMOS33")),
        Resource("chain_in", 0, Pins("E13", dir="i"), Attrs(IO_TYPE="LVCMOS33")),
        Resource("chain_out", 0, Pins("C13", dir="o"), Attrs(IO_TYPE="LVCMOS33")),
    ]


plan = Plat().prepare(Harness(), name="top")
plan.extract({out!r})
print("ELABORATED")
"""

TO_VERILOG = """read_rtlil top.il
hierarchy -top top
flatten
delete t:$scopeinfo
proc
memory_collect
opt_clean
write_verilog -noattr hr.v
"""


def elaborate(out):
    out.mkdir(parents=True, exist_ok=True)
    code = ELABORATE.format(gw=str(GW), out=str(out))
    (out / "elaborate.py").write_text(code)
    cmd = ["bash", "-c", f'source "{OSS_CAD_ENV}" && exec "{sys.executable}" elaborate.py']
    run(cmd, out, dict(os.environ), "yosys", out / "elaborate.log")


def lpf_for_diamond(src, dst):
    """Keep LOCATE/IOBUF; FREQUENCY PORT in MHZ (Diamond rejects HZ); drop NET clocks
    (LSE renames nets; TRACE derives PLL outputs from the input port)."""
    out = []
    for line in src.read_text().splitlines():
        m = re.match(r'\s*FREQUENCY\s+PORT\s+("[^"]+")\s+([\d.eE+]+)\s+HZ\s*;', line)
        if m:
            out.append(f"FREQUENCY PORT {m.group(1)} {float(m.group(2)) / 1e6:g} MHZ;")
        elif re.match(r"\s*FREQUENCY\s+NET", line):
            continue
        else:
            out.append(line)
    out += ["SYSCONFIG CONFIG_IOVOLTAGE=3.3;"]
    dst.write_text("\n".join(out) + "\n")


def diamond(out, speed, paths):
    env = diamond_env()
    run(["bash", "-c", f'source "{OSS_CAD_ENV}" && exec yosys -q -p "script to_verilog.ys"'],
        out, dict(os.environ), "yosys", out / "to_verilog.log")
    lpf_for_diamond(out / "top.lpf", out / "diamond.lpf")
    run(["synthesis", "-a", ARCH, "-d", DEVICE, "-t", PACKAGE, "-s", speed, "-top", "top",
         "-ngd", "top.ngd", "-optimization_goal", "Timing", "-frequency", "180",
         "-ver", "hr.v"], out, env, "synthesis", out / "lse.log")
    run(["map", "top.ngd", "diamond.lpf", "-a", ARCH, "-p", DEVICE, "-t", PACKAGE, "-s", speed,
         "-o", "top_map.ncd", "-pr", "top.prf"], out, env, "map", out / "map.log")
    run(["par", "-w", "-l", "5", "-n", "1", "top_map.ncd", "top_par.dir", "top.prf"],
        out, env, "par", out / "par.log")
    ncd = sorted((out / "top_par.dir").glob("*.ncd"))[0]
    run(["trce", "-v", str(paths), "-sethld", "-o", "top.twr", str(ncd), "top.prf"],
        out, env, "trce", out / "trce.log")
    summarise(out / "top.twr")


def summarise(twr):
    text = twr.read_text(errors="replace")
    summary = text.split("Report Summary", 1)[-1][:6000]
    say(f"TRACE summary ({twr}):\n{summary}")


def nextpnr(out, seed):
    cmd = (f'source "{OSS_CAD_ENV}" && yosys -q -l synth.log -p '
           f'"read_rtlil top.il; synth_ecp5 -top top -nowidelut -cmp2softlogic -json np.json" && '
           f'exec nextpnr-ecp5 --quiet --timing-allow-fail --router router1 --seed {seed} '
           f'--12k --package CABGA256 --speed 8 --json np.json --lpf top.lpf '
           f'--textcfg np.config --log np-seed{seed}.tim')
    run(["bash", "-c", cmd], out, dict(os.environ), "nextpnr", out / f"nextpnr-seed{seed}.log")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--speed", default="6", choices=["6", "7", "8"])
    ap.add_argument("--paths", type=int, default=60, help="trce -v: paths per preference")
    ap.add_argument("--nextpnr", action="store_true", help="open flow on the harness instead")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--reuse", action="store_true", help="skip elaboration if top.il exists")
    args = ap.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    out = ROOT / "tmp" / "hr628" / (f"nextpnr" if args.nextpnr else f"diamond-s{args.speed}")
    if not (args.reuse and (out / "top.il").exists()):
        if out.exists():
            shutil.rmtree(out)
        elaborate(out)
    (out / "to_verilog.ys").write_text(TO_VERILOG)
    if args.nextpnr:
        nextpnr(out, args.seed)
    else:
        diamond(out, args.speed, args.paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
