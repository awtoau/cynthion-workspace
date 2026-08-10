#!/usr/bin/env python3
#
# Which issues talk about a subject but do not carry its label.
# SPDX-License-Identifier: BSD-3-Clause

"""
Finds label gaps in the tracker.

    ./scripts/issue_label_audit.py                        # every subject
    ./scripts/issue_label_audit.py hyperram               # one subject
    ./scripts/issue_label_audit.py hyperram --numbers 313,316  # label these

Output goes to the terminal and to `tmp/logs/issue-label-audit.log`.

## How a match is scored

Two tiers, because a bare keyword hit is noise:

  * **strong** -- the title matches, or the body hits are DENSE enough that the
    issue is about the subject rather than listing it among others.
  * **weak** -- present but sparse. A 23,000-character timeout audit naming
    HyperRAM ten times is a timeout issue.

Density, not a raw count, is what separates them: the broad audits on this
tracker are long, so a count threshold alone promotes every one of them.

**Neither tier is applied automatically.** The strong tier is a candidate list
and it over-fires by design -- an issue that works HyperRAM as its example is
dense in it without being about it. Triage, then `--numbers` the survivors.
"""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "awtoau/cynthion-workspace"

# Title patterns and body patterns per label. Body terms are counted; the
# threshold is what separates "about this" from "mentions this".
SUBJECTS = {
    "hyperram": {
        "title": ("hyperram", "hyper ram", "psram", " dqs", "dqs ", "rwds",
                  "w956a8", "readclksel", "burstdet"),
        "body": ("hyperram", "psram", "rwds", "w956a8", "readclksel",
                 "burstdet", "hyperbus", "dqsbufm"),
    },
    "flash": {
        "title": ("flash", "qspi", "spi nor", "w25q", "bitstream"),
        "body": ("flash", "qspi", "w25q", "winbond", "spiflash",
                 "configuration flash", "flash_divisor"),
    },
    "usb": {
        "title": ("usb", "ulpi", "cdc", "endpoint", "tinyusb", "facedancer",
                  "moondancer", "phy register"),
        "body": ("ulpi", "usb3343", "endpoint", "tinyusb", "cdc-acm",
                 " cdc ", "descriptor", "enumerat", "bulk endpoint"),
    },
    # Deliberately narrower than `riscv`, which covers the softcore, the SoC
    # and the firmware. This is the microarchitecture only.
    "cpu": {
        "title": ("vexii", "vexriscv", "cache", "branch predict", "fmax",
                  "instructions", "superloop"),
        "body": ("vexii", "gshare", "d-cache", "i-cache", "cache line",
                 "branch predictor", "cycles per instruction", "coremark",
                 "fetch-bound"),
    },
    "pins": {
        "title": ("pins", "pmod", "pinout", "slew", "iobuf"),
        "body": ("pmod", "pullmode", "slewrate", "hysteresis", "iostandard",
                 "add_resources", "request(", "pin drive", " .oe"),
    },
    "clocks": {
        "title": ("clock", "clocks", "pll", "fmax", "eclk", "domain"),
        "body": ("ehxpll", "clkos", "clki_div", "clkfb", "eclksync",
                 "clock domain", "solve_pll", "ecp5 pll", "dcsc",
                 "ddrdll", "edge clock"),
    },
}

# Body hits per 1000 characters needed to call an issue "about" the subject.
# 2.0 keeps #228 (7 in 2.7 k) and drops the 23 k timeout audit (10 in 23 k).
DENSITY = 2.0

# `gh issue list` caps well above this; the tracker is nowhere near it.
ISSUE_LIMIT = 500


def fetch(cache: Path, refresh: bool) -> list:
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    out = subprocess.run(
        ["gh", "issue", "list", "--repo", REPO, "--state", "all",
         "--limit", str(ISSUE_LIMIT),
         "--json", "number,title,body,labels,state"],
        capture_output=True, text=True, check=True).stdout
    cache.write_text(out)
    return json.loads(out)


def classify(issue: dict, rule: dict) -> tuple[str, float] | None:
    title = (issue["title"] or "").lower()
    body = (issue["body"] or "").lower()
    hits = sum(body.count(t) for t in rule["body"])
    density = 1000 * hits / max(len(body), 1)
    if any(t in title for t in rule["title"]):
        return "strong", density
    if density >= DENSITY:
        return "strong", density
    if hits:
        return "weak", density
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("subjects", nargs="*", default=None,
                    help="labels to audit; default every subject known")
    ap.add_argument("--numbers",
                    help="comma-separated issues to label, after triage")
    ap.add_argument("--refresh", action="store_true",
                    help="re-fetch instead of using tmp/issues.json")
    args = ap.parse_args()

    log_dir = ROOT / "tmp" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_dir / "issue-label-audit.log"),
                  logging.StreamHandler(sys.stdout)])
    log = logging.getLogger("issue-label-audit")

    issues = fetch(ROOT / "tmp" / "issues.json", args.refresh)
    log.info("%d issues", len(issues))

    wanted = args.subjects or list(SUBJECTS)
    rc = 0
    for label in wanted:
        rule = SUBJECTS.get(label)
        if rule is None:
            log.error("no rule for %r; known: %s", label, ", ".join(SUBJECTS))
            rc = 1
            continue

        buckets = {"strong": [], "weak": []}
        tagged = 0
        for issue in issues:
            has = any(l["name"] == label for l in issue["labels"])
            scored = classify(issue, rule)
            if has:
                tagged += 1
                continue
            if scored:
                buckets[scored[0]].append((issue, scored[1]))

        log.info("%s: %d already labelled, %d strong gaps, %d weak",
                 label, tagged, len(buckets["strong"]), len(buckets["weak"]))
        for tier in ("strong", "weak"):
            for issue, density in sorted(buckets[tier],
                                         key=lambda p: -p[0]["number"]):
                log.info("  %-6s #%-4d %-6s %5.2f/kc  %s", tier,
                         issue["number"], issue["state"], density,
                         issue["title"][:88])

        if args.numbers:
            for number in [n.strip() for n in args.numbers.split(",") if n.strip()]:
                subprocess.run(["gh", "issue", "edit", number, "--repo", REPO,
                                "--add-label", label], check=True)
                log.info("labelled #%s %s", number, label)

    return rc


if __name__ == "__main__":
    sys.exit(main())
