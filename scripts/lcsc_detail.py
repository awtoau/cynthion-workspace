#!/usr/bin/env python3
"""Pull voltage/package/stock attributes from LCSC product detail API for candidate C-codes."""
import json, subprocess, sys

CODES = sys.argv[1:] or [
    "C2948723",   # S27KS0642GABHI020 (C3334999 jlc)
    "C2944469",   # S27KS0642GABHV020 (C3330744 jlc)
    "C17234581",  # IS66WVH16M8DALL-166B1LI (C18363686 jlc)
    "C17595920",  # IS66WVH32M8DALL-166 (C18725156)
    "C1349096",   # IS66WVH8M8BLL-100B1LI (C1439379, 479 stk) -> 3.0V check
    "C1348887",   # IS66WVH16M8ALL-166 (C1439170)
    "C1349099",   # IS66WVH8M8ALL-166 (C1439382)
    "C17672148",  # Winbond W958D8NBYA4I 256Mb
]

def detail(code):
    out = subprocess.run(
        ["curl","-s",f"https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={code}",
         "-H","User-Agent: Mozilla/5.0"], capture_output=True, text=True, timeout=30).stdout
    try:
        r = json.loads(out).get("result") or {}
    except Exception as e:
        return code, {"err": str(e)}
    if not r:
        return code, {"err": "no result"}
    attrs = {}
    for a in (r.get("paramVOList") or []):
        attrs[a.get("paramNameEn")] = a.get("paramValueEn")
    return code, {
        "model": r.get("productModel"),
        "mfr": (r.get("brandNameEn") or ""),
        "stock": r.get("stockNumber"),
        "pkg": r.get("encapStandard"),
        "voltage_attrs": {k: v for k, v in attrs.items() if "olt" in k or "VCC" in k or "Supply" in k},
        "cap_attrs": {k: v for k, v in attrs.items() if "apac" in k or "Memory" in k or "Density" in k or "Size" in k},
        "all_attrs": attrs,
    }

for c in CODES:
    code, d = detail(c)
    print(f"\n===== {code} =====")
    if "err" in d:
        print("  ", d["err"]); continue
    print(f"  model={d['model']}  mfr={d['mfr']}  stock={d['stock']}  pkg={d['pkg']}")
    print(f"  voltage: {d['voltage_attrs']}")
    print(f"  capacity: {d['cap_attrs']}")
