#!/usr/bin/env python3
"""Run ECP5 synthesis->nextpnr timing flow for standalone VexiiRiscv."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VEXII = ROOT / "riscv-64" / "work" / "vexiiriscv"
RTL = VEXII / "VexiiRiscv.v"
WRAP = ROOT / "riscv-64" / "out" / "sim" / "vexii_ecp5_autowrap.v"
OUTDIR = ROOT / "riscv-64" / "out" / "sim"
OUTDIR.mkdir(parents=True, exist_ok=True)

JSON = OUTDIR / "VexiiRiscv_ecp5.json"
TEXTCFG = OUTDIR / "VexiiRiscv_ecp5_out.config"
YOSYS_LOG = OUTDIR / "vexii_ecp5_yosys.log"
NEXTPNR_LOG = OUTDIR / "vexii_ecp5_nextpnr.log"
SUMMARY = OUTDIR / "vexii_ecp5_timing_summary.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="nextpnr thread count (0 uses nextpnr default).",
    )
    return parser.parse_args()


def _width_bits(width: str | None) -> int:
    if not width:
        return 1
    m = re.match(r"\[(\d+)\s*:\s*(\d+)\]", width.strip())
    if not m:
        return 1
    msb = int(m.group(1))
    lsb = int(m.group(2))
    return abs(msb - lsb) + 1


def _const_zero(width: str | None) -> str:
    bits = _width_bits(width)
    if bits <= 1:
        return "1'b0"
    return f"{bits}'b0"


def generate_wrapper(rtl_path: Path, wrap_path: Path) -> None:
    text = rtl_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"module\s+VexiiRiscv\s*\((.*?)\);", text, flags=re.S)
    if not m:
        raise RuntimeError("Could not locate VexiiRiscv module header")

    header = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
    ports: list[tuple[str, str | None, str]] = []
    for raw in header.splitlines():
        line = raw.split("//", 1)[0].strip().rstrip(",")
        if not line:
            continue
        pm = re.match(r"^(input|output)\s+(?:wire|reg)?\s*(\[[^\]]+\])?\s*([A-Za-z0-9_]+)$", line)
        if not pm:
            continue
        direction = pm.group(1)
        width = pm.group(2)
        name = pm.group(3)
        ports.append((direction, width, name))

    all_names = {name for _, _, name in ports}

    decls: list[str] = []
    assigns: list[str] = []
    conns: list[str] = []

    for direction, width, name in ports:
        if name in ("clk", "reset"):
            conns.append(f"    .{name}({name})")
            continue

        width_s = f"{width} " if width else ""
        decls.append(f"  wire {width_s}{name};")
        if direction == "input":
            if name.endswith("_cmd_ready"):
                assigns.append(f"  assign {name} = 1'b1;")
            elif name.endswith("_rsp_valid"):
                peer = name.replace("_rsp_valid", "_cmd_valid")
                if peer in all_names:
                    assigns.append(f"  assign {name} = {peer};")
                else:
                    assigns.append(f"  assign {name} = 1'b0;")
            elif name.endswith("_rsp_payload_id"):
                peer = name.replace("_rsp_payload_id", "_cmd_payload_id")
                if peer in all_names:
                    assigns.append(f"  assign {name} = {peer};")
                else:
                    assigns.append(f"  assign {name} = {_const_zero(width)};")
            elif name.endswith("_rsp_payload_error"):
                assigns.append(f"  assign {name} = 1'b0;")
            elif name.endswith("_rsp_payload_word"):
                assigns.append(f"  assign {name} = 32'h00000013;")
            elif name.endswith("_rsp_payload_data"):
                bits = _width_bits(width)
                if bits == 64:
                    assigns.append(f"  assign {name} = 64'h0000001300000013;")
                elif bits == 32:
                    assigns.append(f"  assign {name} = 32'h00000013;")
                else:
                    assigns.append(f"  assign {name} = {_const_zero(width)};")
            elif "PrivilegedPlugin_logic_rdtime" in name:
                bits = _width_bits(width)
                if bits == 64:
                    assigns.append(f"  assign {name} = rdtime_counter;")
                else:
                    assigns.append(f"  assign {name} = rdtime_counter[{bits-1}:0];")
            elif "_int_" in name:
                assigns.append(f"  assign {name} = 1'b0;")
            else:
                assigns.append(f"  assign {name} = {_const_zero(width)};")
        conns.append(f"    .{name}({name})")

    body = [
        "`timescale 1ns/1ps",
        "",
        "module VexiiRiscvWrap(",
        "  input wire clk,",
        "  input wire reset",
        ");",
        "",
        "  reg [63:0] rdtime_counter = 64'd0;",
        "  always @(posedge clk) begin",
        "    rdtime_counter <= rdtime_counter + 1'b1;",
        "  end",
        "",
    ]
    body.extend(decls)
    body.append("")
    body.extend(assigns)
    body.append("")
    body.append("  VexiiRiscv core (")
    body.append(",\n".join(conns))
    body.append("  );")
    body.append("")
    body.append("endmodule")
    body.append("")

    wrap_path.write_text("\n".join(body), encoding="utf-8")


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    print("$", " ".join(str(x) for x in cmd))
    return subprocess.run(cmd, text=True, capture_output=True)


def main() -> int:
    args = parse_args()

    if not RTL.exists():
        print(f"Missing RTL: {RTL}")
        return 1

    try:
        generate_wrapper(RTL, WRAP)
    except Exception as exc:
        print(f"Failed to generate wrapper: {exc}")
        return 1

    yosys = shutil.which("yosys")
    nextpnr = shutil.which("nextpnr-ecp5")
    if yosys is None or nextpnr is None:
        print("Missing required tools: yosys and/or nextpnr-ecp5")
        return 1

    ys = (
        f"read_verilog {RTL} {WRAP}; "
        f"synth_ecp5 -top VexiiRiscvWrap -json {JSON}; "
        "stat"
    )

    yp = run([yosys, "-q", "-l", str(YOSYS_LOG), "-p", ys])
    if yp.returncode != 0:
        print(f"ECP5 synthesis failed. See {YOSYS_LOG}")
        return yp.returncode

    np_cmd = [
        nextpnr,
        "--12k",
        "--package",
        "CABGA256",
        "--speed",
        "8",
        "--json",
        str(JSON),
        "--textcfg",
        str(TEXTCFG),
        "--timing-allow-fail",
        "--freq",
        "25",
    ]
    if args.threads > 0:
        np_cmd.extend(["--threads", str(args.threads)])

    np = run(np_cmd)
    NEXTPNR_LOG.write_text(np.stdout + np.stderr, encoding="utf-8", errors="replace")
    if np.returncode != 0:
        print(f"nextpnr failed. See {NEXTPNR_LOG}")
        return np.returncode

    log = NEXTPNR_LOG.read_text(encoding="utf-8", errors="replace")
    achieved = re.findall(r"Max frequency for clock '[^']+':\s*([0-9.]+) MHz", log)
    status_line = ""
    for line in log.splitlines():
        if "Info: Critical path" in line or "Info: Max frequency" in line:
            status_line += line + "\n"

    summary_lines = [
        "ECP5 nextpnr timing summary",
        f"rtl={RTL}",
        f"wrap={WRAP}",
        f"json={JSON}",
        f"textcfg={TEXTCFG}",
        f"yosys_log={YOSYS_LOG}",
        f"nextpnr_log={NEXTPNR_LOG}",
    ]
    if achieved:
        summary_lines.append("max_frequencies_mhz=" + ", ".join(achieved))
    if status_line:
        summary_lines.append("key_lines:")
        summary_lines.extend(status_line.rstrip().splitlines())

    SUMMARY.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print(f"Timing summary: {SUMMARY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
