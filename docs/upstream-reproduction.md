# Building upstream's code, unmodified, without destroying this machine

Needed for two things: measuring what upstream's designs actually use, and
validating a patch against **their** tree before sending it (#200 lists this
missing container as a blocker on six prepared PR branches).

`scripts/upstream_build/build.py` is the tool. This file is why it is shaped the
way it is.

## The rule this exists to enforce

**A number from our tree is not a number about upstream.** Our fork carries our
commits, our dependency pins and our fixes. Every comparison, every "does it
fit", every "their build does X" has to come from their code built their way, or
it is a statement about us wearing their name.

That is not a hypothetical. It nearly happened here twice in one afternoon —
see the traps below.

## Why a container and not a venv

`repos/cynthion/BUILD.md`'s install step is:

    pip uninstall -y -r <(pip freeze)

against the active environment. On this machine that is a free-threaded Python
3.15t with the whole workspace installed into it. **A venv is no protection**:
`pip freeze` enumerates what the interpreter can see, and the command is aimed at
whatever is active when it runs.

Upstream also pins Python 3.11 where the host is 3.15t, and ships no Dockerfile,
nix or devcontainer — so there is nothing to inherit and this is the first
reproducible build of their gateware here.

## The traps, in the order they were hit

Each of these produced either a hard failure or — worse — a plausible number
that would have been attributed to upstream.

### 1. `bash -lc` throws away `PATH`

A login shell re-reads `/etc/profile`, which resets `PATH` and drops the venv.
`pip` then resolves to Debian's system pip, which refuses under PEP 668
("externally-managed-environment").

**Fix:** `bash -c`, and name both interpreters by absolute path.

### 2. `repos/cynthion` is OUR fork

The submodule's remote is `awtoau/awto-cynthion`, it carries our commits
(`platform(r1.4): make FPGA_ADV bidirectional with a pull-up`), and its
`pyproject.toml:47` points luna-soc at `awtoau/awto-luna-soc`.

**Building it measures our variant.** The only tell is in the pip output:

    luna-soc-0.3.2+awto.1        <- ours
    luna-soc==0.3.2              <- upstream's

This is the dangerous one. It fails silently — you get a complete, believable
utilisation table for the wrong thing.

**Fix:** the container clones `greatscottgadgets/cynthion` at a pinned tag, and
prints the installed `luna`/`amaranth`/`cynthion` versions so the next reader can
check rather than trust the label.

### 3. `cynthion/python/src/shared` is a symlink

It points at `../../../shared/`, three levels up in the repo. Copying just the
`cynthion/python` subdirectory leaves it dangling, and the package then fails
with:

    ImportError: cannot import name 'usb' from 'cynthion.shared'

**Fix:** build in place inside the clone. Never copy a subtree out of it.

### 4. `Makefile:1` is `SHELL := /bin/zsh`

Not bash. A slim Debian image does not have it, and the failure is
`make: /bin/zsh: No such file or directory`.

**Fix:** `zsh` in the image.

### 5. Amaranth builds in a temp directory

The log says `Build directory: /tmp/tmpXXXXXXXX`, and upstream's Makefile only
copies the `.bit` out with `--output build/facedancer.bit`. Reports —
`top.tim`, `top.rpt` — stay in the temp directory and vanish.

**A bitstream alone cannot answer a utilisation question.** The build has to be
told to keep its files.

## Pinning

The OSS CAD Suite release is pinned by date, not `latest`. An unpinned toolchain
makes every comparison run against a moving denominator — the same mistake the
fabric coverage work already records, where an entire archived sweep's timing
results were discarded because the constraint had moved under them.

The upstream ref is pinned too (`--ref`, default `0.2.5`).

## Isolation

Nothing the container does can reach the working tree:

* the clone is fetched fresh into a **tmpfs**, never a bind mount of ours;
* only `tmp/upstream-build/<target>/` is mounted writable, for build products;
* `tmp/upstream-build/` is gitignored — it is regenerable by definition.

## Using it for patches

The same container validates a patch before it is sent, which is what #200 needs:

1. build the target at the pinned upstream ref — that is the **before**;
2. apply the patch to the clone, build again — the **after**;
3. compare utilisation, timing and behaviour between the two, so the PR quotes
   numbers from upstream's tree rather than ours.

A patch whose evidence comes from our fork is a patch upstream cannot check.
