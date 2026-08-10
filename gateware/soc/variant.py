#!/usr/bin/env python3
#
# Which variant a build IS: the environment that changes what top.py elaborates.
# SPDX-License-Identifier: BSD-3-Clause

"""
One definition of the build variant, shared by the gateware and the runner.

    import variant                  # from gateware/soc/
    from soc import variant         # from scripts/, with gateware/ on the path

    variant.settings()   normalised `NAME=value` list -- what the cache key hashes
    variant.slug()       the same thing as one directory name
    variant.build_dir()  tmp/awto_soc/build/<slug> -- one directory per variant
    variant.flag(name)   resolved boolean, for top.py
    variant.value(name)  resolved string, for top.py

## Why it is one table

`soc_run.py` already hashed this environment into the bitstream cache key while
the build directory stayed a fixed path, so every variant built into the same
directory and two builds could not run at once (#351). A directory derived from
a second list of variables would sit one edit away from disagreeing with the
cache -- a build reusing another variant's artifacts while the cache says it is
fresh.

So `VARIANT_ENV` is the only list, `top.py` reads its own values back through
`flag()`/`value()`, and the defaults exist once.

**Anything `top.py` reads from the environment at import time that changes what
it elaborates belongs here.** `CYNTHION_CLOCK_MIRROR` and its divisor did not,
and they add a pad-driving register in every mirrored domain -- so two builds
either side of that flag hashed identically, and the second was served the
first's bitstream as "built from these exact sources".

## Naming

`bist0-ck100-dqs1-mirror0-mirrordiv4` -- one `tag` + value per entry, in table
order. Legible from `ls` and stable: a rung's directory has the same name every
time it is built, which is what makes revisiting one a cache hit rather than a
resynthesis. Nothing prunes them; see `scripts/soc_build_fanout.py --prune`.
"""

import os
import re
from pathlib import Path

# Flags top.py treats as "" == "0" == off, versus values it parses.
FLAG = "flag"
TEXT = "text"

# (environment variable, default, directory tag, kind)
#
# The defaults are top.py's, and top.py now reads them from here rather than
# restating them -- an unset variable and one set to its default must resolve to
# the same digest, or an ordinary run after a sweep resynthesises for nothing and
# then refuses a `--firmware-only` load as stale when the bitstream is correct.
VARIANT_ENV = (
    ("CYNTHION_HYPERRAM_BIST",      "",    "bist",      FLAG),
    ("CYNTHION_HYPERRAM_CK_MHZ",    "100", "ck",        TEXT),
    ("CYNTHION_HYPERRAM_BIST_DQS",  "1",   "dqs",       FLAG),
    ("CYNTHION_CLOCK_MIRROR",       "",    "mirror",    FLAG),
    ("CYNTHION_CLOCK_MIRROR_DIV",   "4",   "mirrordiv", TEXT),
)

_BY_NAME = {name: (default, tag, kind) for name, default, tag, kind in VARIANT_ENV}


def _resolve(name, env=None):
    """The value top.py will see, normalised. Unknown names are an error."""
    if name not in _BY_NAME:
        raise KeyError(
            f"{name} is not in variant.VARIANT_ENV. Every environment variable "
            f"that changes what top.py elaborates must be listed there, or two "
            f"different bitstreams hash the same and share a build directory.")
    default, _tag, kind = _BY_NAME[name]
    env = os.environ if env is None else env
    raw = env.get(name, default) or default
    if kind is FLAG:
        # "", "0" and unset are one state; anything else is the other. Normalised
        # so `=1` and `=yes` cannot hash differently while elaborating alike.
        return "0" if raw in ("", "0") else "1"
    return raw


def flag(name, env=None):
    return _resolve(name, env) == "1"


def value(name, env=None):
    return _resolve(name, env)


def settings(env=None):
    """The variant as a list of `NAME=value`, in table order."""
    return [f"{name}={_resolve(name, env)}" for name, _d, _t, _k in VARIANT_ENV]


def slug(env=None):
    """The variant as one filesystem-safe directory name."""
    parts = []
    for name, _default, tag, _kind in VARIANT_ENV:
        # A comma (two CK rungs in one bitstream) and a decimal point both reach
        # here; keep the point, which reads, and flatten the rest.
        parts.append(tag + re.sub(r"[^A-Za-z0-9.]", "_", _resolve(name, env)))
    return "-".join(parts)


def build_dir(root, env=None):
    """Where this variant builds. One directory per variant, never shared."""
    return Path(root) / "tmp" / "awto_soc" / "build" / slug(env)


if __name__ == "__main__":
    print(slug())
    for setting in settings():
        print(" ", setting)
