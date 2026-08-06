#!/usr/bin/env python3
#
# Prove the optional-feature spikes cannot reach the shipping image.
# See awtoau/cynthion-workspace#115.
# SPDX-License-Identifier: BSD-3-Clause

"""Check that the spike features change nothing about the shell.

    python3 scripts/soc_feature_isolation_check.py
    python3 scripts/soc_feature_isolation_check.py -v

`firmware/cynthion-soc` carries four optional features -- `rtic`, `usbport`,
`models`, `embassy` -- whose only purpose is to build measurement artefacts in
`src/bin/`. The claim each of them makes is "the shipping image is byte-identical
either way". This checks it rather than asserting it, three ways:

1. **Every `src/bin/*.rs` has a `[[bin]]` entry with `required-features`.**
   Cargo auto-discovers `src/bin/`, so a target without an entry is built by
   the DEFAULT build. That is not hypothetical: an Embassy skeleton with no
   `embassy` dependency in scope reached `./dev.py test` exactly this way.

2. **`src/main.rs` declares no module the spikes own.** A file in `src/` is only
   compiled into the shell if something says `mod`. The spikes reach their
   shared code by `#[path]` from `src/bin/`, which does not.

3. **The shell's `.text` is identical with the features on and off.** The
   empirical half, and the one that would catch a mistake the first two miss.

## Why `.text` and not the whole file

`build.rs` stamps the git hash, the branch name and the dirty bit into the
binary, and this script cannot run without making the tree dirty in the eyes of
the second build. Those land in `.rodata` as strings, so `.rodata` legitimately
differs between two runs of this script and comparing it would fail for a reason
that is not about features. `.text` carries no stamp: if one instruction of the
shell changed, it changes.

Output goes to the terminal and to `tmp/logs/dev.log`.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

CRATE = ROOT / "firmware" / "cynthion-soc"
MANIFEST = CRATE / "Cargo.toml"
BIN_DIR = CRATE / "src" / "bin"
MAIN = CRATE / "src" / "main.rs"

# Its own build directory, so this never overwrites the artefact the bitstream
# packer reads -- the reason soc_test.py keeps one too.
BUILD_DIR = ROOT / "tmp" / "isolation-build"

# The one feature that is about the target rather than about a spike. Never
# counted as a spike feature, and always on here because the QEMU link is what
# this script builds.
TARGET_FEATURE = "qemu"

# The measurement load. A feature that enables this one changes the shell on
# purpose -- see where `spike_features` is computed.
WORKLOAD_FEATURE = "workload"

# Modules only the spikes use. None of these may be declared by src/main.rs.
SPIKE_MODULES = ["usb", "usb_report"]


def declared_features():
    """Feature names from the manifest's `[features]` table."""
    text = MANIFEST.read_text()
    match = re.search(r"^\[features\]\s*$(.*?)(?=^\[)", text,
                      re.MULTILINE | re.DOTALL)
    if not match:
        return set()
    return set(re.findall(r"^([A-Za-z0-9_-]+)\s*=", match.group(1),
                          re.MULTILINE))


def feature_graph():
    """`{feature: [what it enables]}` from the manifest's `[features]` table."""
    text = MANIFEST.read_text()
    match = re.search(r"^\[features\]\s*$(.*?)(?=^\[)", text,
                      re.MULTILINE | re.DOTALL)
    graph = {}
    if not match:
        return graph
    for name, body in re.findall(r"^([A-Za-z0-9_-]+)\s*=\s*\[([^\]]*)\]",
                                 match.group(1), re.MULTILINE | re.DOTALL):
        graph[name] = [part.strip().strip('"')
                       for part in body.split(",") if part.strip()]
    return graph


def enables(feature, graph, seen=None):
    """Every feature `feature` turns on, transitively, including itself."""
    seen = seen if seen is not None else set()
    if feature in seen:
        return seen
    seen.add(feature)
    for other in graph.get(feature, []):
        if "/" not in other:
            enables(other, graph, seen)
    return seen


def bin_targets():
    """`{path: required-features}` for every `[[bin]]` in the manifest."""
    text = MANIFEST.read_text()
    targets = {}
    for block in re.split(r"^\[\[bin\]\]\s*$", text, flags=re.MULTILINE)[1:]:
        # Stop at the next section header.
        block = re.split(r"^\[", block, flags=re.MULTILINE)[0]
        path = re.search(r'^\s*path\s*=\s*"([^"]+)"', block, re.MULTILINE)
        features = re.search(r"^\s*required-features\s*=\s*\[([^\]]*)\]",
                             block, re.MULTILINE)
        if path:
            targets[path.group(1)] = (
                [f.strip().strip('"') for f in features.group(1).split(",")
                 if f.strip()] if features else [])
    return targets


def text_fingerprint(elf):
    """A fingerprint of the shell's CODE, invariant to link order.

    Not a hash of `.text`, and the difference matters. Two builds that differ
    only in which cargo features were enabled produce different `.text` BYTES
    for a reason that is not a difference in the program: `-C metadata`
    includes the feature set, so the crate disambiguator in every mangled
    symbol changes, so symbols sort differently, so every PC-relative call
    encodes a different displacement.

    Measured on this crate with `--features models`, which gates nothing the
    shell compiles: `.text` stayed at exactly 36,136 bytes and 12,389
    instructions, and every one of the 1,432 differing lines was the same
    instruction calling the same symbol under a different disambiguator. Six
    lines differed after that too -- the same six calls, with the target moved
    from a positive displacement to a negative one.

    So this normalises the disassembly and fingerprints the SORTED result.
    Reordering is invisible; an added, removed or altered instruction is not.

    Returns `(size, count, digest)`, or None if the tools are missing.
    """
    objdump = shutil.which("llvm-objdump")
    size_tool = shutil.which("llvm-size")
    if objdump is None or size_tool is None:
        return None

    listing = subprocess.run([objdump, "-d", "--no-show-raw-insn", str(elf)],
                             capture_output=True, text=True)
    if listing.returncode != 0:
        return None

    lines = []
    for line in listing.stdout.splitlines():
        if re.match(r"^\s*[0-9a-f]+:", line):
            line = line.split(":", 1)[1]
        # A function header, `800002d2 <symbol>:`. The address is where the
        # linker put it; the symbol is which function it is.
        line = re.sub(r"^\s*[0-9a-f]{6,16}\s+<", "<", line)
        # The crate disambiguator `Cs<base62>_` and generic instantiation
        # hashes. Both are metadata-derived; neither is the program.
        line = re.sub(r"::h[0-9a-f]{16}", "::hHASH", line)
        line = re.sub(r"Cs[0-9A-Za-z]{9,17}_", "CsHASH_", line)
        # Addresses, and the direction of a PC-relative displacement.
        line = re.sub(r"[-+]?0x[0-9a-f]+", "0xADDR", line)
        # The displacement of an indirect jump, INCLUDING whether it has one:
        # a target that moved from `jalr -0x1a4(ra)` to `jalr ra` is the same
        # call to the same symbol at a different distance. `jalr 0xADDR(ra)`
        # and `jalr ra` both become `jalr ra`. The symbol annotation that
        # follows says which function is being called, and that is compared.
        line = re.sub(r"0xADDR\(([a-z][a-z0-9]*)\)", r"\1", line)
        line = line.strip()
        if line:
            lines.append(line)

    sizes = subprocess.run([size_tool, "-A", str(elf)],
                           capture_output=True, text=True).stdout
    match = re.search(r"^\.text\s+(\d+)", sizes, re.MULTILINE)
    size = int(match.group(1)) if match else -1

    digest = hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()
    return size, len(lines), digest


def build_shell(features):
    """Build the QEMU shell with `features` also on. `(elf, error)`."""
    env = dict(os.environ)
    env["RUSTFLAGS"] = "-C link-arg=-Tmemory-qemu.x -C link-arg=-Tlink.x"
    argv = ["cargo", "build", "--release", "--bin", "cynthion-soc",
            "--features", ",".join(["qemu", *features]),
            "--target-dir", str(BUILD_DIR)]
    proc = subprocess.run(argv, cwd=CRATE, env=env, capture_output=True,
                          text=True)
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout).strip()[-1200:]
    return (BUILD_DIR / "riscv32imac-unknown-none-elf" / "release"
            / "cynthion-soc"), None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    failures = []
    emit("soc_feature_isolation_check: the spikes cannot reach the shell")
    emit("")

    # 1. every src/bin target is declared and gated
    targets = bin_targets()
    features = declared_features()
    required = {f for gate in targets.values() for f in gate}

    # A [[bin]] gated on a feature the manifest does not declare can never be
    # built at all. That is SAFE -- it is excluded from the default build, which
    # is what this script is about -- but it means the target is dead, so it is
    # reported rather than silently passed over.
    orphans = sorted(required - features - {TARGET_FEATURE})
    if orphans:
        for orphan in orphans:
            owners = sorted(p for p, gate in targets.items() if orphan in gate)
            emit(f"  note: `{orphan}` is required by {', '.join(owners)} but "
                 f"is not declared in [features], so that target can never be "
                 f"built (excluded from the default build, which is safe, but "
                 f"it is dead)")
        emit("")

    # Every spike feature that actually exists, turned on together. If the shell
    # survives all of them at once it survives any of them.
    #
    # Minus the ones that turn on `workload`, whose whole purpose is to add the
    # #115 measurement load TO the shell -- `docs/soc-concurrency.md`
    # measures the shell with it and the shipping image is built without it. A
    # feature that gates a `[[bin]]` AND implies `workload`
    # (`wlbare`, `rticwl` and everything built on it) would otherwise be reported
    # as changing the shell, which is true and is not the property this checks.
    # Computed from the manifest rather than listed, so a new one is classified
    # by what it enables.
    graph = feature_graph()
    workload_features = {name for name in features
                         if WORKLOAD_FEATURE in enables(name, graph)}
    spike_features = sorted((required & features)
                            - {TARGET_FEATURE} - workload_features)
    if workload_features & required:
        emit(f"  note: {', '.join(sorted(workload_features & required))} "
             f"imply `{WORKLOAD_FEATURE}`, which adds the #115 load to the "
             f"shell by design; not checked for inertness")
    declared = {(CRATE / path).resolve() for path in targets}
    for source in sorted(BIN_DIR.glob("*.rs")):
        entry = next((p for p in targets
                      if (CRATE / p).resolve() == source.resolve()), None)
        if entry is None:
            failures.append(
                f"{source.relative_to(ROOT)} has no [[bin]] entry, so cargo "
                f"auto-discovers it and the DEFAULT build compiles it")
            continue
        if not targets[entry]:
            failures.append(
                f"{source.relative_to(ROOT)} has a [[bin]] entry with no "
                f"required-features, so the default build compiles it")
        elif args.verbose:
            emit(f"  ok: {source.name} gated on {targets[entry]}")
    if not failures:
        emit(f"  ok: all {len(list(BIN_DIR.glob('*.rs')))} src/bin targets are "
             f"declared with required-features")
    _ = declared

    # 2. the shell declares none of the spike modules
    main_text = MAIN.read_text()
    for module in SPIKE_MODULES:
        if re.search(rf"^\s*(pub\s+)?mod\s+{module}\s*;", main_text,
                     re.MULTILINE):
            failures.append(
                f"src/main.rs declares `mod {module};`, so the shell compiles "
                f"a module only the spikes use")
    else:
        emit(f"  ok: src/main.rs declares none of {SPIKE_MODULES}")

    # 3. the shell's code does not move, feature by feature and then together
    #
    # One at a time, because "all of them together changed it" does not say
    # which, and the answer is the interesting part: a feature that only adds a
    # [[bin]] cannot touch the shell, but one that also turns on a feature of a
    # SHARED dependency can. `rtic` is exactly that shape -- it enables
    # `riscv/critical-section-single-hart` on a crate the shell links -- so it
    # is the one worth watching.
    baseline, error = build_shell([])
    reference = None
    if error:
        failures.append(f"the shell did not build with no features:\n{error}")
    else:
        reference = text_fingerprint(baseline)
        if reference is None:
            failures.append("llvm-objdump or llvm-size is missing, so the "
                            "shell's code could not be fingerprinted")
        else:
            size, count, digest = reference
            emit(f"  ok: baseline shell is {size:,} bytes of .text, "
                 f"{count:,} instructions")
            emit(f"      fingerprint {digest[:32]}")

    if reference:
        gates = [[feature] for feature in spike_features]
        if len(spike_features) > 1:
            gates.append(spike_features)
        for gate in gates:
            name = gate[0] if len(gate) == 1 else "all together"
            elf, error = build_shell(gate)
            if error:
                failures.append(f"the shell did not build with {gate}:\n{error}")
                continue
            got = text_fingerprint(elf)
            if got == reference:
                emit(f"  ok: `{name}` leaves the shell's code identical")
            else:
                size, count, digest = got
                failures.append(
                    f"`{name}` CHANGES the shell's code\n"
                    f"      with it:    {size:,} bytes, {count:,} instructions, "
                    f"{digest[:32]}\n"
                    f"      without it: {reference[0]:,} bytes, "
                    f"{reference[1]:,} instructions, {reference[2][:32]}\n"
                    f"      (the shipping image is built without it, so the "
                    f"product is unaffected -- but the feature is not inert, "
                    f"and anything built WITH it is not the shell that ships)")

    emit("")
    if failures:
        for failure in failures:
            emit(f"  FAIL: {failure}")
        emit("")
        emit(f"RESULT: FAIL - {len(failures)} problem(s)")
        return 1
    emit("RESULT: PASS - the shipping image is unchanged by every spike feature")
    return 0


if __name__ == "__main__":
    sys.exit(main())
