#!/usr/bin/env python3.15t
"""Harvest Lattice Diamond 3.14 usage/property/help/strings data for ECP5 comparison.

Mines a local Diamond install for capabilities the open ECP5 flow (yosys /
nextpnr-ecp5 / prjtrellis ecppack) does not expose.  Every finding is attributed
to the ispfpga device *tree* it came from, because a finding under e.g. `or5g00`
(ORCA) or `xo2c00` (MachXO2) says nothing about ECP5.

ECP5 trees:  ep5c00  (LFE5U / LFE5UM, incl. LFE5U-12F/25F on Cynthion)
             ep5c00a (ECP5-5G, LFE5UM5G)

Outputs (all under <worktree>/tmp/diamond-mine/):
  usg/<tree>__<name>.usg        verbatim copies of every .usg
  prp/<tree>__<name>.prp        verbatim copies of every .prp
  stf/<name>.stf                Diamond strategy/property definitions
  help/<binary>.txt             captured --help / usage output
  strings/<binary>.txt          filtered strings output
  index.json                    structured index of everything harvested

Log: <worktree>/tmp/logs/diamond_usage_harvest.log
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

DIAMOND = Path.home() / "lscc" / "diamond" / "3.14"
WORKTREE = Path(__file__).resolve().parent.parent
OUT = WORKTREE / "tmp" / "diamond-mine"
LOGDIR = WORKTREE / "tmp" / "logs"

BINDIRS = [
    DIAMOND / "bin" / "lin64",
    DIAMOND / "ispfpga" / "bin" / "lin64",
]

# ECP5 device trees.  Anything else is a different family.
ECP5_TREES = {"ep5c00", "ep5c00a"}

log = logging.getLogger("diamond_harvest")


def setup_logging() -> None:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    logfile = LOGDIR / "diamond_usage_harvest.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    fh = logging.FileHandler(logfile, mode="w")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.setLevel(logging.DEBUG)
    log.addHandler(fh)
    log.addHandler(sh)
    log.info("logging to %s", logfile)


def diamond_env() -> dict[str, str]:
    """Replicate bin/lin64/diamond_env explicitly (see that script)."""
    bindir = DIAMOND / "bin" / "lin64"
    fpgadir = DIAMOND / "ispfpga"
    fpgabindir = fpgadir / "bin" / "lin64"
    env = dict(os.environ)
    env["LSC_DIAMOND"] = "true"
    env["QT_PLUGIN_PATH"] = ""
    env["NEOCAD_MAXLINEWIDTH"] = "32767"
    env["FOUNDRY"] = str(fpgadir)
    env["TCL_LIBRARY"] = str(DIAMOND / "tcltk" / "lib" / "tcl8.5")
    env["PATH"] = f"{bindir}:{fpgabindir}:{env.get('PATH', '')}"
    env["LD_LIBRARY_PATH"] = f"{bindir}:{fpgabindir}"
    env["LM_LICENSE_FILE"] = str(DIAMOND / "license" / "license.dat")
    env.pop("LSC_INI_PATH", None)
    # Several Diamond "CLI" binaries are really Qt/wx apps that block forever
    # waiting on an X server.  Point DISPLAY at nothing so they fail fast with
    # "cannot connect to X server" instead of hanging the harvest.
    env["DISPLAY"] = ""
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("XAUTHORITY", None)
    return env


def tree_of(path: Path) -> str:
    """Return the ispfpga device tree a data file belongs to."""
    parts = path.parts
    try:
        i = parts.index("ispfpga")
    except ValueError:
        return "(non-ispfpga)"
    nxt = parts[i + 1]
    return "(common)" if nxt == "data" else nxt


def harvest_datafiles(env: dict[str, str]) -> dict:
    """Copy every .usg and .prp, plus .stf strategy files, attributed by tree."""
    index: dict[str, list] = {"usg": [], "prp": [], "stf": []}

    for ext in ("usg", "prp"):
        dest = OUT / ext
        dest.mkdir(parents=True, exist_ok=True)
        for src in sorted(DIAMOND.rglob(f"*.{ext}")):
            tree = tree_of(src)
            text = src.read_text(errors="replace")
            out = dest / f"{tree}__{src.name}"
            out.write_text(text)
            index[ext].append(
                {
                    "tree": tree,
                    "is_ecp5": tree in ECP5_TREES,
                    "source": str(src),
                    "copy": str(out),
                    "bytes": len(text),
                    "lines": text.count("\n") + 1,
                }
            )
            log.debug("%s [%s] %s (%d bytes)", ext, tree, src.name, len(text))

    dest = OUT / "stf"
    dest.mkdir(parents=True, exist_ok=True)
    for src in sorted((DIAMOND / "data").glob("*.stf")):
        text = src.read_text(errors="replace")
        (dest / src.name).write_text(text)
        index["stf"].append({"source": str(src), "bytes": len(text)})
        log.debug("stf %s (%d bytes)", src.name, len(text))

    log.info(
        "harvested %d .usg, %d .prp, %d .stf",
        len(index["usg"]),
        len(index["prp"]),
        len(index["stf"]),
    )
    return index


def run_bitgen_arch_help(env: dict[str, str]) -> dict:
    """`bitgen -help <arch>` prints architecture-specific usage.

    This is richer and more current than the static ep5c00/data/bitgen.usg,
    which has not been touched since 2008.  Capture every architecture so the
    ECP5 set can be diffed against the other families.
    """
    dest = OUT / "bitgen-arch"
    dest.mkdir(parents=True, exist_ok=True)
    bitgen = DIAMOND / "ispfpga" / "bin" / "lin64" / "bitgen"

    # Discover the valid architecture list from bitgen's own error message.
    probe = subprocess.run(
        [str(bitgen), "-help", "__invalid__"],
        env=env,
        cwd=str(OUT),
        capture_output=True,
        text=True,
        errors="replace",
        stdin=subprocess.DEVNULL,
    )
    blob = (probe.stdout or "") + (probe.stderr or "")
    (dest / "_arch_list.txt").write_text(blob)
    archs: list[str] = []
    seen_header = False
    for line in blob.splitlines():
        if "Valid architectures are" in line:
            seen_header = True
            continue
        if seen_header and line.strip():
            archs.append(line.strip())
    log.info("bitgen knows %d architectures: %s", len(archs), archs)

    results = []
    for arch in archs:
        cp = subprocess.run(
            [str(bitgen), "-help", arch],
            env=env,
            cwd=str(OUT),
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
        text = (cp.stdout or "") + (cp.stderr or "")
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", arch)
        (dest / f"{safe}.txt").write_text(text)
        # Isolate the "-g <opt:val>" option table for easy diffing.
        gopts = []
        in_g = False
        for line in text.splitlines():
            if "-g <opt:val>" in line:
                in_g = True
                continue
            if in_g and line.strip():
                gopts.append(line.strip())
        results.append(
            {"arch": arch, "out": str(dest / f"{safe}.txt"), "g_options": gopts}
        )
        log.info("bitgen -help %-20s -> %d bytes, %d -g opts", arch, len(text), len(gopts))

    return {"bitgen_arch_help": results}


def is_elf(p: Path) -> bool:
    try:
        with p.open("rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def run_help(env: dict[str, str]) -> dict:
    """Run each CLI tool with no args / -help and capture usage output."""
    dest = OUT / "help"
    dest.mkdir(parents=True, exist_ok=True)
    results = []

    # These block on an X display or run as daemons; they never return usage.
    skip = {
        "lmgrd",
        "cableservermain",
        "cableserver",
        "diamond",
        "diamondc",
        "debugger",
        "deployment",
        "dbgmain",
        "ddtmain",
        "ddtcmain",
        "ddtcmd",
        "launchmicosystem",
        "ipexpress",
        "ipxwrapper",
        "uninstaller",
        "fileutility",
        "lattice",
        "medit",
        "memedit",
        "ebredit",
        "revealedf",
        "thermalanalysis",
        "ssoana",
        "lstipc",
        "synthesis",
        "expwrap",
        "orcapp",
        "qp",
    }

    for bindir in BINDIRS:
        for exe in sorted(bindir.iterdir()):
            if not exe.is_file() or not os.access(exe, os.X_OK):
                continue
            if exe.suffix in {".so", ".ico", ".dat", ".bash", ".csh"}:
                continue
            if ".so." in exe.name:
                continue
            if exe.name in skip:
                log.info("skip %s (interactive/daemon)", exe.name)
                continue

            captured = []
            for args in ([], ["-help"], ["--help"], ["-h"]):
                try:
                    cp = subprocess.run(
                        [str(exe), *args],
                        env=env,
                        cwd=str(OUT),
                        capture_output=True,
                        stdin=subprocess.DEVNULL,
                        text=True,
                        errors="replace",
                        # These tools print their usage text and exit within
                        # milliseconds.  Anything still running after 10s is a
                        # GUI app blocked on a display or an interactive prompt,
                        # never a usage message still being produced -- kill it
                        # so one bad binary cannot stall the whole harvest.
                        timeout=10,
                    )
                except subprocess.TimeoutExpired:
                    captured.append(f"### argv={args} TIMEOUT (blocked, killed)\n")
                    log.warning("%s %s blocked; killed", exe.name, args)
                    break
                except OSError as e:
                    captured.append(f"### argv={args} EXEC-ERROR {e}\n")
                    break
                blob = (cp.stdout or "") + (cp.stderr or "")
                captured.append(
                    f"### argv={args} rc={cp.returncode} len={len(blob)}\n{blob}\n"
                )
                # A long response to bare invocation is usually the full usage.
                if not args and len(blob) > 400:
                    break

            text = f"# {exe}\n" + "\n".join(captured)
            outp = dest / f"{bindir.name}__{exe.name}.txt"
            outp.write_text(text)
            results.append(
                {"binary": str(exe), "out": str(outp), "bytes": len(text)}
            )
            log.info("help %-24s -> %6d bytes", exe.name, len(text))

    log.info("captured help for %d binaries", len(results))
    return {"help": results}


# Terms worth flagging when strings-mining.
INTEREST = re.compile(
    r"bitstream|bitgen|readback|multiboot|multi-boot|encrypt|decrypt|AES|key file"
    r"|partial|reconfig|SPImode|spi_?mode|compress|verify|jedec|jed|security"
    r"|fuse|OTP|feature ?row|background|golden|failsafe|dual ?boot|CRC"
    r"|prog_?done|donephase|goephase|gsrphase|gwdphase|CfgMode|RamCfg"
    r"|SysConfig|sysCONFIG|MSPI|SLAVE_SPI|MASTER_SPI|persistent"
    r"|WAKE_UP|wakeup|freq|CONFIG_MODE|INBUF|TransFrEQ|Trans(FR|fr)",
    re.IGNORECASE,
)

OPTLIKE = re.compile(r"^-{1,2}[A-Za-z][A-Za-z0-9_]{1,30}$")


def run_strings(env: dict[str, str]) -> dict:
    """strings-mine binaries for option flags / config messages."""
    dest = OUT / "strings"
    dest.mkdir(parents=True, exist_ok=True)
    results = []

    targets = []
    for bindir in BINDIRS:
        for exe in sorted(bindir.iterdir()):
            if not exe.is_file() or not is_elf(exe):
                continue
            targets.append(exe)

    for exe in targets:
        try:
            cp = subprocess.run(
                ["strings", "-n", "4", str(exe)],
                capture_output=True,
                text=True,
                errors="replace",
            )
        except OSError as e:
            log.warning("strings failed on %s: %s", exe, e)
            continue
        lines = cp.stdout.splitlines()
        opts = sorted({l for l in lines if OPTLIKE.match(l)})
        hits = sorted({l.strip() for l in lines if INTEREST.search(l) and 4 < len(l) < 300})
        if not opts and not hits:
            continue
        text = (
            f"# {exe}\n## option-like strings ({len(opts)})\n"
            + "\n".join(opts)
            + f"\n\n## interesting strings ({len(hits)})\n"
            + "\n".join(hits)
            + "\n"
        )
        outp = dest / f"{exe.parent.name}__{exe.name}.txt"
        outp.write_text(text)
        results.append(
            {
                "binary": str(exe),
                "out": str(outp),
                "n_opts": len(opts),
                "n_hits": len(hits),
            }
        )
        log.info("strings %-28s opts=%-4d hits=%d", exe.name, len(opts), len(hits))

    log.info("strings-mined %d binaries", len(results))
    return {"strings": results}


def cross_reference() -> dict:
    """Capture ecppack / nextpnr-ecp5 surface for comparison."""
    dest = OUT / "openflow"
    dest.mkdir(parents=True, exist_ok=True)
    out = {}

    ecppack = Path.home() / ".local/bin/ecppack"
    if ecppack.exists():
        cp = subprocess.run(
            ["strings", "-n", "3", str(ecppack)],
            capture_output=True,
            text=True,
            errors="replace",
        )
        (dest / "ecppack.strings.txt").write_text(cp.stdout)
        cph = subprocess.run(
            [str(ecppack), "--help"],
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
        (dest / "ecppack.help.txt").write_text((cph.stdout or "") + (cph.stderr or ""))
        out["ecppack"] = str(dest / "ecppack.help.txt")
        log.info("captured ecppack help+strings")
    else:
        log.warning("ecppack not found at %s", ecppack)

    for name in ("nextpnr-ecp5",):
        exe = Path.home() / ".local/bin" / name
        if not exe.exists():
            log.warning("%s not found", name)
            continue
        cp = subprocess.run(
            [str(exe), "--help"],
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
        (dest / f"{name}.help.txt").write_text((cp.stdout or "") + (cp.stderr or ""))
        out[name] = str(dest / f"{name}.help.txt")
        log.info("captured %s help", name)

    return {"openflow": out}


def main() -> int:
    setup_logging()
    OUT.mkdir(parents=True, exist_ok=True)
    env = diamond_env()
    log.info("FOUNDRY=%s", env["FOUNDRY"])
    log.info("LM_LICENSE_FILE=%s", env["LM_LICENSE_FILE"])

    index: dict = {"diamond": str(DIAMOND), "ecp5_trees": sorted(ECP5_TREES)}
    index.update(harvest_datafiles(env))
    index.update(run_bitgen_arch_help(env))
    index.update(run_help(env))
    index.update(run_strings(env))
    index.update(cross_reference())

    (OUT / "index.json").write_text(json.dumps(index, indent=2))
    log.info("index written to %s", OUT / "index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
