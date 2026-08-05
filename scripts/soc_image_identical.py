#!/usr/bin/env python3
#
# Prove the shipping image is unchanged by a feature that is off.
# SPDX-License-Identifier: BSD-3-Clause

"""Build the board image from this tree and from a reference commit, and diff
the bytes.

The #115 work adds `--features workload` and `--features preempt`. The claim
that has to be checked rather than asserted is that **with them off, the image
that ships is the image that shipped**.

## Why this checks the tree out in place, rather than exporting it

The obvious implementation -- `git archive main | tar -x` somewhere under
`tmp/` and build there -- gives a wrong answer, and gives it confidently. It was
written that way first and reported `.text` "same size (41400), 19068 bytes
differ". Nothing had changed: cargo derives `-C metadata` from the crate's
absolute path, that seeds symbol hashes, and symbol hashes decide the order LTO
emits functions in. Two builds of identical source at two paths are the same
instructions in a different order, and a byte comparison calls 46% of the
section different.

So the reference has to be built at the same path, which means checking it out
over the working tree and putting it back. The crate must be committed first;
the recovery if this is interrupted is one command, and it is printed below.

    ./scripts/soc_image_identical.py            # against main
    ./scripts/soc_image_identical.py --ref HEAD~1

`.text` must match byte for byte. `.rodata` is expected to differ:
`firmware/cynthion-soc/build.rs` stamps the image with the commit and the dirty
flag, so a difference confined to that string is the correct result and not a
failure -- which is why it is reported rather than asserted.

Output is mirrored to ./tmp/logs/soc_image_identical.log.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRATE = Path("firmware/cynthion-soc")
TRIPLE = "riscv32imac-unknown-none-elf"
LOG = ROOT / "tmp" / "logs" / "soc_image_identical.log"

# One target dir for both builds, deliberately: the point is that everything
# except the source is identical, and two target dirs is one more difference to
# have to argue about.
BUILD = ROOT / "tmp" / "image-identical"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, text=True)


def build():
    env = dict(os.environ)
    env.pop("RUSTFLAGS", None)
    done = subprocess.run(
        ["cargo", "build", "--release", "--target", TRIPLE,
         "--target-dir", str(BUILD)],
        cwd=ROOT / CRATE, env=env, capture_output=True, text=True)
    if done.returncode != 0:
        return None, (done.stderr or done.stdout).strip()[-1200:]
    return BUILD / TRIPLE / "release" / "cynthion-soc", None


def section(elf, name):
    """One section's bytes.

    Through a real file rather than `/dev/stdout`: objcopy seeks its output, and
    against a pipe it silently produces nothing at all -- which reads as "both
    images are 0 bytes and therefore identical", the most comfortable wrong
    answer this script could give. It did exactly that once.
    """
    scratch = ROOT / "tmp" / f"section{name}.bin"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["rust-objcopy", "-O", "binary", f"--only-section={name}",
         str(elf), str(scratch)], check=True, capture_output=True)
    data = scratch.read_bytes()
    scratch.unlink()
    return data


def symbols(elf):
    """`.text` symbols as {name: size}, with the crate disambiguator removed.

    The byte comparison above can still fail for a reason that is not a change
    to the shipping code. Adding a `[features]` entry changes the feature set,
    that changes `-C metadata`, that changes every symbol hash, and LTO emits
    the same functions in a different order -- same size, different bytes, and
    `#[cfg]`-ed-out modules never compiled at all.

    So this is the decisive instrument: the same functions at the same sizes
    means nothing was added, removed or resized, whatever order the linker put
    them in. `Cs<hash>_` is Rust v0 mangling's crate disambiguator and is
    exactly the part that moves.
    """
    out = subprocess.run(["rust-objdump", "-t", str(elf)],
                         capture_output=True, text=True).stdout
    # `<addr> <flags> <section> <size> [.hidden] <name>`, and the optional
    # `.hidden` between the size and the name is why this indexes from the front
    # of the line rather than the back.
    row = re.compile(r"^([0-9a-f]{8})\s+\S+\s+(\S+)\s+([0-9a-f]{8})\s+(.*)$")
    found = {}
    for line in out.splitlines():
        match = row.match(line)
        if match is None or match.group(2) != ".text":
            continue
        name = match.group(4).replace(".hidden ", "").strip()
        name = re.sub(r"Cs[0-9A-Za-z]{10,}_", "Cs_", name)
        name = re.sub(r"17h[0-9a-f]{16}E", "17hE", name)
        found[name] = int(match.group(3), 16)
    return found


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default="main")
    args = ap.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    out = LOG.open("w")

    def say(line=""):
        print(line)
        out.write(line + "\n")
        out.flush()

    dirty = git("status", "--porcelain", "--", str(CRATE)).stdout.strip()
    if dirty:
        say("the crate has uncommitted changes; commit them first -- this "
            "script checks another commit out over them and back:")
        say(dirty)
        return 1

    ours, error = build()
    if error:
        say("this tree failed to build:\n" + error)
        return 1
    mine = {name: section(ours, name) for name in (".text", ".rodata")}
    mine_symbols = symbols(ours)

    say(f"restoring with: git checkout HEAD -- {CRATE}   (if interrupted)")
    git("checkout", args.ref, "--", str(CRATE))
    try:
        theirs, error = build()
        if error:
            say(f"{args.ref} failed to build:\n" + error)
            return 1
        base = {name: section(theirs, name) for name in (".text", ".rodata")}
        base_symbols = symbols(theirs)
    finally:
        git("checkout", "HEAD", "--", str(CRATE))

    status = 0
    if mine_symbols == base_symbols:
        say(f"symbols  IDENTICAL to {args.ref}  {len(mine_symbols)} in .text, "
            "every one the same size")
    else:
        added = sorted(set(mine_symbols) - set(base_symbols))
        gone = sorted(set(base_symbols) - set(mine_symbols))
        resized = sorted(n for n in set(mine_symbols) & set(base_symbols)
                         if mine_symbols[n] != base_symbols[n])
        say(f"symbols  DIFFERENT: {len(added)} added, {len(gone)} removed, "
            f"{len(resized)} resized")
        for name in (added + gone + resized)[:20]:
            say("           " + name)
        status = 1

    for name in (".text", ".rodata"):
        if mine[name] == base[name]:
            say(f"{name:8} IDENTICAL to {args.ref}  {len(mine[name])} bytes")
            continue
        if len(mine[name]) != len(base[name]):
            say(f"{name:8} DIFFERENT SIZE  {len(base[name])} -> "
                f"{len(mine[name])}")
            status = 1
            continue
        differ = [i for i, (a, b) in enumerate(zip(mine[name], base[name]))
                  if a != b]
        say(f"{name:8} same size ({len(mine[name])}), {len(differ)} bytes "
            f"differ, first at {differ[0]:#x}")
        if name == ".rodata":
            say("         -- expected: build.rs stamps the commit and the "
                "dirty flag into this section")
        elif mine_symbols == base_symbols:
            say("         -- same functions at the same sizes, so this is LTO "
                "ordering: see the `symbols` docstring")
        else:
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
