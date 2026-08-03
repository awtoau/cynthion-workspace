#!/usr/bin/env python3
#
# Search a Claude Code session transcript for findings on a topic.
# SPDX-License-Identifier: BSD-3-Clause

"""
Pulls topic-matching passages out of a session transcript.

A long session's transcript is tens of megabytes of JSONL -- far past what fits
in a context window -- so recovering "what did we establish about X" cannot be
done by reading it. This extracts only the matching passages.

It exists because a summarised session loses detail: the summary keeps
conclusions and drops the measurements behind them. When the task is to write
those measurements onto an issue, the transcript is the only remaining source.

Assistant text and user text are searched; tool results are skipped by default
because they are mostly build logs that swamp the signal.

    ./scripts/transcript_grep.py <transcript.jsonl> --terms ms MHz
    ./scripts/transcript_grep.py <t.jsonl> --terms SCK --context 400
    ./scripts/transcript_grep.py <t.jsonl> --terms DMA --role user
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "transcript_grep.log"


def texts(entry, include_tools):
    """Yield (role, text) for every text block in one transcript entry."""
    message = entry.get("message")
    if not isinstance(message, dict):
        return
    role = message.get("role", "?")
    content = message.get("content")

    if isinstance(content, str):
        yield role, content
        return
    if not isinstance(content, list):
        return

    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            yield role, block.get("text", "")
        elif kind == "thinking":
            yield role + ":thinking", block.get("thinking", "")
        elif kind == "tool_result" and include_tools:
            payload = block.get("content")
            if isinstance(payload, str):
                yield "tool", payload
            elif isinstance(payload, list):
                for part in payload:
                    if isinstance(part, dict) and part.get("type") == "text":
                        yield "tool", part.get("text", "")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--terms", nargs="+", required=True,
                        help="case-insensitive substrings; any match hits")
    parser.add_argument("--context", type=int, default=260,
                        help="characters either side of a match")
    parser.add_argument("--role", help="only this role (user, assistant)")
    parser.add_argument("--include-tools", action="store_true",
                        help="also search tool results (noisy)")
    parser.add_argument("--max", type=int, default=400,
                        help="stop after this many hits")
    args = parser.parse_args()

    if not args.transcript.exists():
        print(f"no transcript at {args.transcript}")
        return 1

    pattern = re.compile("|".join(re.escape(t) for t in args.terms),
                         re.IGNORECASE)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    hits = 0
    seen = set()

    with LOG.open("w") as log:
        def emit(text):
            print(text, flush=True)
            log.write(text + "\n")

        emit(f"searching {args.transcript.name} for {args.terms}")
        emit("")

        with args.transcript.open() as handle:
            for number, line in enumerate(handle, 1):
                if hits >= args.max:
                    emit(f"stopped at --max {args.max}")
                    break
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                for role, text in texts(entry, args.include_tools):
                    if not text or (args.role and not role.startswith(args.role)):
                        continue
                    for match in pattern.finditer(text):
                        start = max(0, match.start() - args.context)
                        end = min(len(text), match.end() + args.context)
                        excerpt = " ".join(text[start:end].split())

                        # The same passage often matches several terms; report
                        # it once rather than once per term.
                        key = excerpt[:120]
                        if key in seen:
                            continue
                        seen.add(key)

                        hits += 1
                        emit(f"--- line {number} [{role}] ---")
                        emit(excerpt)
                        emit("")
                        break

        emit(f"{hits} passages")
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
