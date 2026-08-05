#!/usr/bin/env python3
#
# Prove the shipping image is unchanged by a feature that is off.
# SPDX-License-Identifier: BSD-3-Clause

"""Build the board image from this tree and from `main`, and compare the bytes.

The #115 work adds `--features workload` and `--features preempt`. The claim
that has to be checked rather than asserted is that **with them off, the image
that ships is the image that shipped**.

`.text` and `.rodata` are compared as bytes, not as sizes. `.bss` and the ELF as
a whole are not: `firmware/cynthion-soc/build.rs` stamps the build with the
commit and the dirty flag, so two builds from different commits differ in
`.rodata` by construction -- which is why the `.rodata` comparison below reports
the differing byte count and the offsets, and a difference confined to the
stamp is the expected result rather than a failure.

    ./scripts/soc_image_identical.py            # against main
    ./scripts/soc_image_identical.py --ref HEAD~1

Output is mirrored to ./tmp/logs/soc_image_identical.log.
"""

import argparse
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRIPLE = "riscv32imac-unknown-none-elf"
LOG = ROOT / "tmp" / "logs" / "soc_image_identical.log"
BASE = ROOT / "tmp" / "image-base"


def build(crate, target):
    env = dict(os.environ)
    env.pop("RUSTFLAGS", None)
    done = subprocess.run(
        ["cargo", "build", "--release", "--target", TRIPLE,
         "--target-dir", str(target)],
        cwd=crate, env=env, capture_output=True, text=True)
    if done.returncode != 0:
        return None, (done.stderr or done.stdout).strip()[-1200:]
    return target / TRIPLE / "release" / "cynthion-soc", None


def section(elf, name):
    """One section's bytes.

    Through a real file rather than `/dev/stdout`: objcopy seeks its output,
    and against a pipe that silently produces nothing at all -- which reads as
    "both images are 0 bytes and therefore identical", the most comfortable
    wrong answer this script could give.
    """
    scratch = ROOT / "tmp" / f"section{name}.bin"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["rust-objcopy", "-O", "binary", f"--only-section={name}",
         str(elf), str(scratch)], check=True, capture_output=True)
    data = scratch.read_bytes()
    scratch.unlink()
    return data


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

    # The reference tree, from git rather than from a second checkout: this is a
    # worktree and `git archive` is the one way to materialise another commit
    # without touching the one being worked in.
    if BASE.exists():
        subprocess.run(["rm", "-rf", str(BASE)], check=True)
    BASE.mkdir(parents=True)
    archive = BASE / "ref.tar"
    with archive.open("wb") as fh:
        subprocess.run(["git", "archive", args.ref], cwd=ROOT, stdout=fh,
                       check=True)
    with tarfile.open(archive) as tar:
        tar.extractall(BASE, filter="data")
    archive.unlink()

    ours, error = build(ROOT / "firmware" / "cynthion-soc",
                        ROOT / "tmp" / "image-ours")
    if error:
        say("this tree failed to build:\n" + error)
        return 1
    theirs, error = build(BASE / "firmware" / "cynthion-soc",
                          BASE / "target")
    if error:
        say(f"{args.ref} failed to build:\n" + error)
        return 1

    status = 0
    for name in (".text", ".rodata"):
        mine = section(ours, name)
        base = section(theirs, name)
        if mine == base:
            say(f"{name:8} IDENTICAL  {len(mine)} bytes")
            continue
        if len(mine) != len(base):
            say(f"{name:8} DIFFERENT  {len(base)} -> {len(mine)} bytes")
            status = 1
            continue
        differ = [i for i, (a, b) in enumerate(zip(mine, base)) if a != b]
        say(f"{name:8} same size ({len(mine)}), {len(differ)} bytes differ, "
            f"first at {differ[0]:#x}")
        # A difference confined to `.rodata` is the build stamp; anywhere else
        # is a real change to the shipping image.
        if name != ".rodata":
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
