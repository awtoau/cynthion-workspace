#!/usr/bin/env python3
"""Query JLCPCB SMT parts API for 1.8V HyperRAM drop-in candidates for S27KS0641."""
import json
import subprocess
import sys

URL = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"

KEYWORDS = [
    # Infineon/Cypress S27KS (1.8V)
    "S27KS0641", "S27KS0642", "S27KS", "S70KS1281", "S70KS",
    # ISSI 1.8V ALL suffix
    "IS66WVH", "IS66WVH8M8", "IS66WVH16M8", "IS67WVH",
    # Winbond
    "W958", "W959",
    # AP Memory HyperRAM
    "APS6408", "APS12808", "APS1604", "APS6404", "APM HyperRAM", "APS25616",
    # generic
    "HyperRAM", "HyperBus",
]


def query(keyword):
    payload = json.dumps({"currentPage": 1, "pageSize": 50, "keyword": keyword})
    try:
        out = subprocess.run(
            ["curl", "-s", URL,
             "-H", "Content-Type: application/json",
             "-H", "User-Agent: Mozilla/5.0",
             "-X", "POST", "-d", payload],
            capture_output=True, text=True, timeout=30,
        ).stdout
        data = json.loads(out)
    except Exception as e:
        return keyword, [], f"ERROR: {e}"
    lst = (data.get("data") or {}).get("componentPageInfo", {}).get("list", []) or []
    rows = []
    for c in lst:
        prices = c.get("componentPrices") or []
        p10 = None
        for p in prices:
            if p["startNumber"] <= 10 <= (p["endNumber"] if p["endNumber"] != -1 else 10**9):
                p10 = p["productPrice"]
                break
        rows.append({
            "opn": c.get("erpComponentName"),
            "lcsc": "C" + str(c.get("componentId")),
            "type": c.get("componentTypeEn"),
            "libType": c.get("componentLibraryType"),
            "stock": c.get("stockCount"),
            "p10": p10,
            "isBuy": c.get("isBuyComponent"),
            "noBuyReason": c.get("noBuyReason"),
            "url": c.get("lcscGoodsUrl"),
        })
    return keyword, rows, None


def main():
    seen = {}
    for kw in KEYWORDS:
        keyword, rows, err = query(kw)
        print(f"\n===== keyword: {keyword} =====")
        if err:
            print(err)
            continue
        if not rows:
            print("  (no results)")
        for r in rows:
            key = r["lcsc"]
            if key in seen:
                continue
            seen[key] = r
            print(f"  {r['opn']:32s} {r['lcsc']:12s} stk={str(r['stock']):>7s} "
                  f"p10={r['p10']} lib={r['libType']} buy={r['isBuy']} type={r['type']}")
            if r["noBuyReason"]:
                print(f"      noBuyReason: {r['noBuyReason']}")
    print("\n\n===== UNIQUE SUMMARY (sorted by stock) =====")
    for r in sorted(seen.values(), key=lambda x: -(x["stock"] or 0)):
        print(f"  {r['opn']:34s} {r['lcsc']:12s} stk={str(r['stock']):>7s} "
              f"p10={r['p10']} {r['libType']} {r['type']} :: {r['url']}")


if __name__ == "__main__":
    main()
