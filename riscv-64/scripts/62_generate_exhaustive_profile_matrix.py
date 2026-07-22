#!/usr/bin/env python3
"""Generate an exhaustive profile matrix config for core and SoC variants.

This creates valid feature combinations for:
- Core generator (vexiiriscv.Generate)
- MicroSoC generator (vexiiriscv.soc.micro.MicroSocGen)

Combinations are constrained to avoid unsupported/pointless variants:
- `lsu_l1` requires `fetch_l1`
- advanced features (`btb`, `gshare`, `ras`, `dual_issue`) require both caches
"""

from __future__ import annotations

import argparse
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_OUT_BY_XLEN = {
    64: ROOT / "riscv-64" / "config" / "profile_matrix_exhaustive.json",
    32: ROOT / "riscv-64" / "config" / "profile_matrix_exhaustive_x32.json",
}

FEATURE_KEYS = ["fetch_l1", "lsu_l1", "btb", "gshare", "ras", "dual_issue"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlen", type=int, choices=[32, 64], default=64, help="ISA xlen")
    parser.add_argument(
        "--with-supervisor",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable supervisor/user ISA extensions in base profile",
    )
    parser.add_argument(
        "--with-rva",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable RVA atomic extension in base profile",
    )
    parser.add_argument(
        "--with-rdtime",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable rdtime counter extension in base profile",
    )
    parser.add_argument(
        "--soc-jtag-tap",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable JTAG TAP in generated SoC profiles",
    )
    parser.add_argument("--out", type=pathlib.Path, default=None, help="Output config JSON")
    return parser.parse_args()


def base_args(xlen: int, with_supervisor: bool, with_rva: bool, with_rdtime: bool) -> list[str]:
    args = [
        "--xlen",
        str(xlen),
        "--with-rvm",
        "--with-rvc",
    ]
    if with_supervisor:
        args.append("--with-supervisor")
    if with_rva:
        args.append("--with-rva")
    if with_rdtime:
        args.append("--with-rdtime")
    return args


def base_tag_stem(xlen: int, with_supervisor: bool, with_rva: bool, with_rdtime: bool) -> str:
    parts = [f"x{xlen}"]
    if with_supervisor:
        parts.append("sv")
    if with_rva:
        parts.append("rva")
    parts.extend(["rvm", "rvc"])
    if with_rdtime:
        parts.append("rdtime")
    return "_".join(parts)


def valid_combo(flags: dict[str, bool]) -> bool:
    if flags["lsu_l1"] and not flags["fetch_l1"]:
        return False
    advanced = flags["btb"] or flags["gshare"] or flags["ras"] or flags["dual_issue"]
    if advanced and not (flags["fetch_l1"] and flags["lsu_l1"]):
        return False
    if (flags["gshare"] or flags["ras"]) and not flags["btb"]:
        return False
    return True


def valid_soc_combo(flags: dict[str, bool], with_rva: bool) -> bool:
    # MicroSoc cacheless LSU path cannot carry AMO transactions.
    # When RVA is enabled, require LSU L1 so AMO flows through cached LSU plugin.
    if with_rva and not flags["lsu_l1"]:
        return False
    return True


def feature_tokens(flags: dict[str, bool]) -> list[str]:
    tokens: list[str] = []
    if flags["fetch_l1"]:
        tokens.append("i4k")
    if flags["lsu_l1"]:
        tokens.append("d4k")
    if flags["btb"]:
        tokens.append("btb")
    if flags["gshare"]:
        tokens.append("gshare")
    if flags["ras"]:
        tokens.append("ras")
    if flags["dual_issue"]:
        tokens.append("dual")
    return tokens


def sort_key(flags: dict[str, bool]) -> tuple[int, tuple[int, ...]]:
    bits = tuple(1 if flags[k] else 0 for k in FEATURE_KEYS)
    return (sum(bits), bits)


def all_flag_combos() -> list[dict[str, bool]]:
    combos: list[dict[str, bool]] = []
    for fetch in [False, True]:
        for lsu in [False, True]:
            for btb in [False, True]:
                for gshare in [False, True]:
                    for ras in [False, True]:
                        for dual in [False, True]:
                            flags = {
                                "fetch_l1": fetch,
                                "lsu_l1": lsu,
                                "btb": btb,
                                "gshare": gshare,
                                "ras": ras,
                                "dual_issue": dual,
                            }
                            if valid_combo(flags):
                                combos.append(flags)
    return sorted(combos, key=sort_key)


def args_with_features(
    xlen: int,
    is_soc: bool,
    flags: dict[str, bool],
    with_supervisor: bool,
    with_rva: bool,
    with_rdtime: bool,
    soc_jtag_tap: bool,
) -> list[str]:
    args = base_args(xlen, with_supervisor, with_rva, with_rdtime)
    if flags["fetch_l1"]:
        args += ["--with-fetch-l1", "--fetch-l1-sets", "64", "--fetch-l1-ways", "1"]
    if flags["lsu_l1"]:
        args += ["--with-lsu-l1", "--lsu-l1-sets", "64", "--lsu-l1-ways", "1"]
    if flags["btb"]:
        args += ["--with-btb"]
    if flags["gshare"]:
        args += ["--with-gshare"]
    if flags["ras"]:
        args += ["--with-ras"]
    if flags["dual_issue"]:
        args += ["--dual-issue"]
    if is_soc:
        args += ["--jtag-tap", "true" if soc_jtag_tap else "false"]
    return args


def core_profile(
    xlen: int,
    flags: dict[str, bool],
    idx: int,
    with_supervisor: bool,
    with_rva: bool,
    with_rdtime: bool,
    soc_jtag_tap: bool,
) -> dict[str, object]:
    tokens = feature_tokens(flags)
    suffix = "_" + "_".join(tokens) if tokens else "_base"
    stem = base_tag_stem(xlen, with_supervisor, with_rva, with_rdtime)
    return {
        "name": f"core_exh_{idx:02d}",
        "kind": "core_dev",
        "sbt_main": "vexiiriscv.Generate",
        "sbt_args": args_with_features(
            xlen,
            False,
            flags,
            with_supervisor,
            with_rva,
            with_rdtime,
            soc_jtag_tap,
        ),
        "tag": f"core_{stem}" + suffix,
        "notes": "core exhaustive " + (" + ".join(tokens) if tokens else "base"),
    }


def soc_profile(
    xlen: int,
    flags: dict[str, bool],
    idx: int,
    with_supervisor: bool,
    with_rva: bool,
    with_rdtime: bool,
    soc_jtag_tap: bool,
) -> dict[str, object]:
    tokens = feature_tokens(flags)
    middle = "_".join(tokens)
    stem = base_tag_stem(xlen, with_supervisor, with_rva, with_rdtime)
    tag = f"soc_{stem}"
    if middle:
        tag += "_" + middle
    tag += "_clint_uart"

    out_suffix = "_".join(tokens) if tokens else "base"
    return {
        "name": f"soc_exh_{idx:02d}",
        "kind": "microsoc_direct",
        "sbt_main": "vexiiriscv.soc.micro.MicroSocGen",
        "sbt_args": args_with_features(
            xlen,
            True,
            flags,
            with_supervisor,
            with_rva,
            with_rdtime,
            soc_jtag_tap,
        ),
        "top_module": "MicroSoc",
        "output_prefix": f"microsoc_exh_{idx:02d}_{out_suffix}",
        "tag": tag,
        "notes": "soc exhaustive " + (" + ".join(tokens) if tokens else "base") + " + clint + uart",
    }


def main() -> int:
    args = parse_args()
    out_path = args.out or DEFAULT_OUT_BY_XLEN[args.xlen]

    combos = all_flag_combos()
    profiles: list[dict[str, object]] = []
    for idx, flags in enumerate(combos, start=1):
        profiles.append(
            core_profile(
                args.xlen,
                flags,
                idx,
                args.with_supervisor,
                args.with_rva,
                args.with_rdtime,
                args.soc_jtag_tap,
            )
        )
    soc_combos = [flags for flags in combos if valid_soc_combo(flags, args.with_rva)]
    for idx, flags in enumerate(soc_combos, start=1):
        profiles.append(
            soc_profile(
                args.xlen,
                flags,
                idx,
                args.with_supervisor,
                args.with_rva,
                args.with_rdtime,
                args.soc_jtag_tap,
            )
        )

    payload = {"profiles": profiles}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {out_path}")
    print(f"xlen: {args.xlen}")
    print(
        "base: "
        + base_tag_stem(args.xlen, args.with_supervisor, args.with_rva, args.with_rdtime)
        + f" soc_jtag_tap={args.soc_jtag_tap}"
    )
    print(f"Core profiles: {len(combos)}")
    print(f"SoC profiles: {len(soc_combos)}")
    print(f"Total profiles: {len(profiles)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
