#!/usr/bin/env python3
#
# Record what every gateware build cost, into Postgres, so it can be plotted. (#232)
# SPDX-License-Identifier: BSD-3-Clause

"""
Area, timing and full configuration for every SoC build, in a database.

    ./dev.py metrics status         # is the server up, and how many rows are spooled
    ./dev.py metrics record         # record the build sitting in tmp/vexii_hello/build
    ./dev.py metrics report         # current vs previous vs best-ever vs branch point
    ./dev.py metrics graph          # trend graphs, written to reports/
    ./dev.py metrics flush          # push spooled records once the server is back
    ./dev.py metrics show           # the last N rows, one line each

## Why this exists

Every build produces numbers and then throws them away. `soc_run.py` printed the
fmax lines and discarded the rest of nextpnr's output -- LUT, FF, BRAM, DSP and
IO utilisation all went past unread -- so "did that change cost area?" could only
be answered by rebuilding the parent commit by hand, and "42 of 56 block RAMs"
had to be counted out of a placed bitstream because nothing had recorded it.

The numbers that did survive survived as prose in issues and docs, where nothing
can plot them and nothing marks them stale.

## Three things this refuses to do

**It does not estimate.** Every utilisation figure is parsed out of nextpnr's own
`Device utilisation:` block in `top.tim`. The per-instance block RAM breakdown is
read from the netlist nextpnr was given (`top.json`), so `ram.mem` costing 32 of
the 44 blocks is a fact about this build rather than an inference from a total.

**It does not report a point where the truth is a distribution.** Placement on
this design swings from the high 60s to the mid 70s MHz between runs of identical
source. A single fmax figure has repeatedly been written down as though it were a
property of the design when it is a property of one placement, so `report` gives
n, min, median, mean, sd and max over the builds sharing this exact configuration
and says so when n is 1.

**It does not quietly write somewhere else.** If Postgres is unreachable -- or is
reachable but the database has not been pointed at its tablespace on the 2 TB
drive -- the record is spooled to `tmp/build-metrics/pending/` as JSON, the
failure is printed in full with the exact commands that fix it, and `status`
exits non-zero until the spool is empty. There is no SQLite fallback and no
second connection string to try. A metrics system that silently falls back to a
different store is worse than one that stops, because the series it leaves behind
is the one nobody knows is incomplete.

## Postgres, and where its files are allowed to live

Driver and library are fixed by the awto Python rule and checked at connect time
rather than assumed: pure-Python **psycopg 3** (`psycopg.pq.__impl__ ==
"python"`) and **SQLAlchemy 2.0 Core built without its C extension**. Either
compiled build silently re-pins the GIL for the whole process, which on this
free-threaded interpreter would quietly serialise the parallel build path that
the rest of the workspace is written around -- a regression with no error message
attached, so it is asserted here instead.

Storage is fixed by the awto database rule: the system drive is nearly full, so
the data may not land under the server's own `data_directory`. The database must
have `default_tablespace` set, and that tablespace must live on a data drive,
which is checked on every connection -- because the failure mode of getting it
wrong is not an error either, it is a full root filesystem three months from now.

WHERE that drive is differs per machine and this repository is public, so the
directory is not written down here. `tablespace_root` in
`~/.config/cynthion-workspace/metrics.json` (or `$CYNTHION_METRICS_TABLESPACE_ROOT`)
carries it, alongside the password that has the same reason for living outside
the repo, and the recipe below is printed with it filled in.

## The schema grows itself

The `builds` table declares only identity -- when, which commit, which branch,
dirty or not, how long. Every measured column and every configuration column is
added by `ensure_columns()` on first sight, with its type taken from the Python
value. Adding `HYPERRAM_BIST` to `gateware/soc/top.py` therefore adds a
`hyperram_bist` column to the table on the next build and needs no migration, no
DDL and no edit here. Same for a new VexiiRiscv generator flag.

That is deliberate and it is the reason the table is allowed to be as wide as it
likes: a column per feature is what makes "which change cost the fmax" a query
instead of a bisect.

## Where the configuration comes from

Imported, not restated. `gateware/soc/top.py` and `gateware/soc/cpu/cpu.py` are
the source of every flag, so this reads their module-level constants and parses
`GENERATE_FLAGS` into one column each. A copy of those values kept here would be
wrong the first time somebody changed one, and wrong in the direction that makes
the whole series lie.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "tmp" / "vexii_hello" / "build"
SPOOL = ROOT / "tmp" / "build-metrics" / "pending"
# Rendered reports go to a ROOT-LEVEL `reports/`, not under `tmp/`.
#
# `tmp/` is scratch and `./dev.py clean` empties it. A trend graph is the one
# artefact here whose whole value is that it accumulates -- wiping it defeats
# the point of recording builds at all. The database is the durable store, so
# nothing is lost either way, but the rendered page should not be somewhere a
# cleanup deletes.
#
# The SPOOL stays in `tmp/`: it is genuinely transient, holding records only
# until the server is reachable.
OUTDIR = ROOT / "reports"

sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit, log  # noqa: E402

# What this project builds. A column rather than an assumption, so a second
# design recorded here later is separable by query instead of by memory.
DEFAULT_TARGET = "soc"

# How many same-configuration builds a distribution is taken over by default.
# Twenty is roughly a day's iteration; the point of the window is that a change
# to an unrelated flag starts a new population rather than polluting this one.
DISTRIBUTION_WINDOW = 20

# What the recipe says when nobody has told this machine where its data drive is.
# The awto database rule gives every database a tablespace under one shared
# parent on a drive with room; the parent's actual path names a particular
# machine's disks, so it is configuration rather than a constant here.
UNKNOWN_TABLESPACE_ROOT = "<tablespace_root: the shared parent on a data drive>"


class MetricsUnavailable(RuntimeError):
    """The database could not be used. Carries the message to print."""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def settings() -> dict:
    """Where the database is, from config file then environment.

    Defaults to the local Postgres over its unix socket as the invoking user,
    because that is what is running here and it needs no password: `host` is a
    directory rather than an address, which is how libpq is told to use a socket.
    `~/.config/cynthion-workspace/metrics.json` overrides it and lives outside
    the repo so a password is never a candidate for `git add`.
    """
    cfg = {
        "host": "/var/run/postgresql",
        "port": 5432,
        "user": os.environ.get("USER") or "postgres",
        "password": "",
        "database": "cynthion_metrics",
        "tablespace": "cynthion_metrics_ts",
        # Empty by default and deliberately so: the answer is a mount point on
        # one machine, this repository is public, and `private_path_check.py`
        # is right to reject it in a tracked file.
        "tablespace_root": "",
    }
    path = Path.home() / ".config" / "cynthion-workspace" / "metrics.json"
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text()))
        except json.JSONDecodeError as exc:
            raise MetricsUnavailable(f"{path} is not valid JSON: {exc}") from exc
    for key in list(cfg):
        env = os.environ.get("CYNTHION_METRICS_" + key.upper())
        if env is not None:
            cfg[key] = int(env) if key == "port" else env
    return cfg


def how_to_create(cfg: dict, reason: str) -> str:
    """The message a missing database gets. It names the commands, not the theory.

    Every one of these needs root, which is exactly why they are printed rather
    than attempted: this runs inside a build, and a build is not a thing that
    should be acquiring privilege. The recipe is the awto database rule's, not an
    invention here -- directory under the one shared parent, owned by postgres
    and 700, SELinux-labelled so the server does not flood the audit log, then
    the tablespace and the database pointed at it.
    """
    db = cfg["database"]
    space = cfg["tablespace"]
    user = cfg["user"]
    root = cfg["tablespace_root"] or UNKNOWN_TABLESPACE_ROOT
    return (
        f"Postgres is up but the '{db}' database is not usable as '{user}'.\n"
        f"  {reason}\n"
        f"\n"
        f"Creating it needs root. These are the commands, in order:\n"
        f"    sudo mkdir -p {root}/{db}\n"
        f"    sudo chown postgres:postgres {root}/{db}\n"
        f"    sudo chmod 700 {root}/{db}\n"
        f"    sudo semanage fcontext -a -t postgresql_db_t "
        f"\"{root}(/.*)?\"\n"
        f"    sudo restorecon -Rv {root}\n"
        f"    sudo -u postgres psql -c \"CREATE TABLESPACE {space} "
        f"LOCATION '{root}/{db}'\"\n"
        f"    sudo -u postgres psql -c \"GRANT CREATE ON TABLESPACE {space} "
        f"TO {user}\"\n"
        f"    createdb -D {space} {db}\n"
        f"    psql -d {db} -c \"ALTER DATABASE {db} "
        f"SET default_tablespace='{space}'\"\n"
        f"\n"
        f"The last two run as {user}; the rest are root. The tablespace is not\n"
        f"optional -- the system drive is nearly full, and a database left on it\n"
        f"fills it silently. Nothing has been written to any other store: no\n"
        f"SQLite file, no second database. Records taken meanwhile are spooled\n"
        f"as JSON under {SPOOL.relative_to(ROOT)}/ and go in on the next\n"
        f"`./dev.py metrics flush`."
        + ("" if cfg["tablespace_root"] else
           f"\n\nNOBODY HAS TOLD THIS MACHINE WHERE ITS DATA DRIVE IS, so the "
           f"parent above is\na placeholder. Put the real one in "
           f"~/.config/cynthion-workspace/metrics.json as\n"
           f'{{"tablespace_root": "..."}} -- outside the repo, because it names '
           f"this machine's\ndisks and the repo is public.")
    )


def check_drivers() -> None:
    """The two libraries, and that they are the builds that keep the GIL off.

    Asserted rather than assumed because both failure modes are silent. A
    `pip install psycopg[binary]` or a wheel-installed SQLAlchemy loads a C
    extension that re-pins the GIL for the entire process -- no warning, no
    exception, just the free-threaded build quietly behaving like 3.13 for every
    thread in the program, including the parallel synthesis path that has nothing
    to do with metrics.
    """
    try:
        import psycopg                                       # noqa: PLC0415
    except ImportError as exc:
        raise MetricsUnavailable(
            f"psycopg is not importable on {sys.executable}: {exc}\n"
            f"    python3 -m pip install --break-system-packages psycopg\n"
            f"  (the pure package. NEVER psycopg[binary] or psycopg[c]: the "
            f"compiled\n"
            f"   backend re-enables the GIL for the whole process.)") from exc
    if psycopg.pq.__impl__ != "python":
        raise MetricsUnavailable(
            f"psycopg is the '{psycopg.pq.__impl__}' implementation, not "
            f"'python'.\n"
            f"  The compiled backend re-pins the GIL for this whole process.\n"
            f"    python3 -m pip uninstall psycopg-binary psycopg-c\n"
            f"    python3 -m pip install --break-system-packages psycopg")
    try:
        from sqlalchemy.util import has_compiled_ext          # noqa: PLC0415
    except ImportError as exc:
        raise MetricsUnavailable(
            f"SQLAlchemy is not importable on {sys.executable}: {exc}\n"
            f"    DISABLE_SQLALCHEMY_CEXT=1 python3 -m pip install "
            f"--break-system-packages --no-binary SQLAlchemy "
            f"\"SQLAlchemy>=2.0\"") from exc
    if has_compiled_ext():
        raise MetricsUnavailable(
            "SQLAlchemy loaded its cyextension C module, which is not "
            "free-threading aware\n"
            "  and re-enables the GIL. SQLALCHEMY_DISABLE_CYEXTENSION=1 does "
            "NOT help --\n"
            "  the module still loads. Reinstall from source with it off:\n"
            "    python3 -m pip uninstall SQLAlchemy\n"
            "    DISABLE_SQLALCHEMY_CEXT=1 python3 -m pip install "
            "--break-system-packages --no-binary SQLAlchemy "
            "\"SQLAlchemy>=2.0\"")
    if getattr(sys, "_is_gil_enabled", lambda: False)():
        raise MetricsUnavailable(
            "the GIL is enabled on this interpreter after importing psycopg "
            "and SQLAlchemy.\n"
            "  Something loaded a compiled extension that re-pinned it. This "
            "is the exact\n"
            "  regression the two checks above exist to catch, so it is a "
            "failure rather\n"
            "  than a warning. `python3 scripts/check.py freethreading`.")


def drivers_ok() -> bool:
    """`check_drivers` as a yes/no, for the selftest. Prints the reason if no."""
    try:
        check_drivers()
    except MetricsUnavailable as exc:
        emit(f"        {str(exc).splitlines()[0]}")
        return False
    return True


def engine_url(cfg: dict):
    """The `postgresql+psycopg://` URL, socket or TCP.

    libpq takes a unix socket as a *directory* in `host`, which a URL cannot
    carry in the authority section, so it goes in the query string instead. The
    socket is the default here because it is what peer authentication uses, and
    peer authentication is what makes this work with no password on disk.
    """
    from sqlalchemy import URL                                # noqa: PLC0415
    host = str(cfg["host"])
    if host.startswith("/"):
        return URL.create("postgresql+psycopg", username=cfg["user"],
                          database=cfg["database"],
                          query={"host": host, "port": str(cfg["port"])})
    return URL.create("postgresql+psycopg", username=cfg["user"],
                      password=cfg["password"] or None, host=host,
                      port=int(cfg["port"]), database=cfg["database"])


def check_tablespace(conn, cfg: dict) -> None:
    """The database must write to the 2 TB drive, and this is where that is proved.

    `default_tablespace` is a database-level setting, so it is visible in any
    session as `current_setting`. Checked on every connection rather than at
    creation time: an `ALTER DATABASE ... RESET default_tablespace` by anybody,
    or a database recreated by hand without it, would otherwise start filling the
    system drive with nothing at all saying so.

    Two things are asserted, and the first is the portable one: the setting must
    name a real tablespace with a location of its own. `pg_default` and
    `pg_global` answer the empty string for their location, because they ARE the
    server's data directory -- so "has a location" is exactly "is somewhere
    else", on any machine and without a path written down here. Comparing against
    the server's own `data_directory` would say it more directly and is not
    available: reading that setting needs `pg_read_all_settings`, which a build
    should not be holding.

    If this machine has additionally been told where its shared parent is, the
    location must be under that -- which is what actually pins it to the drive
    with the room.
    """
    root = cfg["tablespace_root"]
    current = list(conn.execute("SELECT current_setting('default_tablespace')"))
    name = current[0][0] if current else ""
    if not name:
        raise MetricsUnavailable(how_to_create(
            cfg, "default_tablespace is unset, so every table would be created "
                 "in pg_default -- the server's own data directory, on the "
                 "system drive."))
    rows = list(conn.execute(
        "SELECT pg_tablespace_location(oid) FROM pg_tablespace "
        "WHERE spcname = :name", {"name": name}))
    location = str(rows[0][0]) if rows else ""
    if not location.startswith("/"):
        raise MetricsUnavailable(how_to_create(
            cfg, f"default_tablespace is '{name}', which has no location of its "
                 f"own -- it is the server's data directory, on the system "
                 f"drive."))
    if root and not location.startswith(root.rstrip("/") + "/"):
        raise MetricsUnavailable(how_to_create(
            cfg, f"default_tablespace is '{name}' at '{location}', which is not "
                 f"under this machine's configured tablespace_root."))


def connect(create: bool = True):
    """A connection with the schema present, or `MetricsUnavailable`.

    The database is never created from here. Postgres cannot create one from
    inside a connection to it, and more to the point creating it correctly means
    a tablespace on the 2 TB drive, which means root -- so the answer to a
    missing database is the printed recipe, not a `CREATE DATABASE` that would
    put it in the wrong place.
    """
    cfg = settings()
    check_drivers()
    from sqlalchemy import create_engine                      # noqa: PLC0415
    from sqlalchemy.pool import NullPool                      # noqa: PLC0415

    engine = create_engine(
        engine_url(cfg),
        # Seconds, and the reason is that a build must not stall on a metrics
        # write: a local server over a unix socket either answers immediately or
        # is not there at all. Anything longer is time added to every build for
        # no information.
        connect_args={"connect_timeout": 3},
        # One connection, used once, closed. A pool would hold a session open
        # across a synthesis run for no benefit.
        poolclass=NullPool)
    try:
        conn = Db(engine)
    except Exception as exc:            # SQLAlchemy wraps several psycopg types
        raise MetricsUnavailable(
            how_to_create(cfg, f"{type(exc).__name__}: "
                               f"{str(exc).splitlines()[0]}")) from exc
    try:
        check_tablespace(conn, cfg)
        if create:
            ensure_schema(conn)
            conn.commit()
    except MetricsUnavailable:
        conn.close()
        raise
    except Exception as exc:
        conn.close()
        raise MetricsUnavailable(
            how_to_create(cfg, f"{type(exc).__name__}: "
                               f"{str(exc).splitlines()[0]}")) from exc
    return conn


class Db:
    """A SQLAlchemy 2.0 Core `Connection`, in the three calls this file needs.

    Core rather than the ORM, and 2.0 commit-as-you-go rather than implicit
    autocommit: `execute` takes a statement and a dict of bound parameters and
    nothing here ever interpolates a value into SQL. Identifiers are a different
    problem -- a self-growing schema has to name a column it has only just
    decided on -- and `ident()` is what makes that safe.

    It exists as a class at all so that `RecordingConnection` below can stand in
    for it exactly, and the dry run therefore drives the real `ensure_columns`
    and `insert` rather than a description of them.
    """

    def __init__(self, engine):
        self._conn = engine.connect()

    def execute(self, sql: str, params: dict | None = None):
        from sqlalchemy import text                           # noqa: PLC0415
        return self._conn.execute(text(sql), params or {})

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def ident(name: str) -> str:
    """A column or table name, quoted, having first proved it is one.

    The schema names columns after Python constants and generator flags, so the
    names arrive from `top.py` and `cpu.py` rather than from a literal in this
    file. Every one of them is checked against this pattern before it reaches
    SQL -- both because an identifier cannot be a bound parameter, and because
    the same name is used as the bind key, where SQLAlchemy requires exactly
    this shape anyway.
    """
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
        raise ValueError(f"not a usable column name: {name!r}")
    return f'"{name}"'


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# Identity only. Everything measured and every configuration flag is added by
# `ensure_columns` the first time it is seen, which is what keeps a new feature
# from needing a migration.
CORE_SQL = [
    """
    CREATE TABLE IF NOT EXISTS builds (
      id            BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
      built_at      TIMESTAMPTZ NOT NULL,
      target        TEXT        NOT NULL,
      status        TEXT        NOT NULL,
      git_ref       TEXT        NOT NULL,
      git_short     TEXT,
      branch        TEXT,
      dirty         BOOLEAN     NOT NULL,
      config_hash   TEXT        NOT NULL,
      build_seconds DOUBLE PRECISION,
      host          TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS build_cells (
      build_id  BIGINT NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
      bel       TEXT   NOT NULL,
      used      BIGINT NOT NULL,
      available BIGINT NOT NULL,
      pct       DOUBLE PRECISION,
      PRIMARY KEY (build_id, bel)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS build_timing (
      build_id       BIGINT NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
      clock          TEXT   NOT NULL,
      constraint_mhz DOUBLE PRECISION,
      achieved_mhz   DOUBLE PRECISION,
      passed         BOOLEAN,
      slack_pct      DOUBLE PRECISION,
      path_from      TEXT,
      path_to        TEXT,
      PRIMARY KEY (build_id, clock)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS build_bram (
      build_id BIGINT NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
      instance TEXT   NOT NULL,
      grp      TEXT   NOT NULL,
      blocks   BIGINT NOT NULL,
      PRIMARY KEY (build_id, instance)
    )
    """,
    # Postgres has no inline index declaration, so these are statements of their
    # own rather than KEY clauses. All three are the WHERE and ORDER BY of the
    # queries below: newest-first listing, "was this commit ever built", and the
    # same-configuration population a distribution is taken over.
    "CREATE INDEX IF NOT EXISTS k_built_at ON builds (built_at)",
    "CREATE INDEX IF NOT EXISTS k_git_ref ON builds (git_ref)",
    "CREATE INDEX IF NOT EXISTS k_config ON builds (config_hash, built_at)",
    "CREATE INDEX IF NOT EXISTS k_group ON build_bram (build_id, grp)",
]

# How the DDL above spells a type against how `information_schema` reports it
# back. The dry run compares the two, so they have to be in the same vocabulary:
# a column declared DOUBLE PRECISION comes back as "double precision", and one
# declared TIMESTAMPTZ comes back as "timestamp with time zone".
DDL_TO_CATALOG = {
    "text": "text",
    "boolean": "boolean",
    "bigint": "bigint",
    "double": "double precision",
    "timestamptz": "timestamp with time zone",
}


def ensure_schema(conn) -> None:
    for sql in CORE_SQL:
        conn.execute(sql)


def core_columns() -> dict:
    """The columns `builds` is declared with, read out of the DDL above.

    Parsed rather than listed a second time. The dry run needs to know what a
    real server would already report as present, and a hand-kept copy of that
    list would drift from the CREATE TABLE it is meant to mirror -- at which
    point the dry run stops describing what actually happens, which is the only
    thing it is for.
    """
    body = CORE_SQL[0].split("(", 1)[1]
    found = {}
    for line in body.splitlines():
        match = re.match(r"\s*(\w+)\s+(\w+)", line.strip())
        if match and match.group(1).upper() not in ("KEY", "PRIMARY",
                                                    "FOREIGN", "UNIQUE"):
            kind = match.group(2).lower()
            found[match.group(1)] = DDL_TO_CATALOG.get(kind, kind)
    return found


def sql_type(value) -> str:
    """The Postgres type for a Python value.

    Strings are always TEXT. Postgres charges nothing for it and imposes no
    length, which removes the whole VARCHAR(n) question -- a value that grows
    past the declared width neither truncates nor errors, because there is no
    declared width to grow past.
    """
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "BIGINT"
    if isinstance(value, float):
        return "DOUBLE PRECISION"
    return "TEXT"


def ensure_columns(conn, table: str, row: dict) -> None:
    """Add whatever columns `row` needs that `table` does not have.

    THIS is the "no migration dance" the issue asks for. A new flag in
    `gateware/soc/top.py` becomes a new column on the next build, named after the
    constant, and every historic row keeps NULL there -- which is the correct
    answer for "what was this flag set to before it existed".

    The catalogue is still read first, even though `ADD COLUMN IF NOT EXISTS`
    would make the add safe on its own, because the answer is needed for the
    second half: a column first seen holding an integer that later receives a
    float is widened to DOUBLE PRECISION rather than rounding every future value
    into an integer column. `IF NOT EXISTS` then covers the gap between the probe
    and the ALTER, which two builds running at once would otherwise lose a race
    in. A metrics table that quietly rounds is a metrics table that lies.
    """
    have = {name: kind.lower() for name, kind in conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = :table",
        {"table": table})}
    for name, value in row.items():
        column = ident(name)
        if name not in have:
            conn.execute(f"ALTER TABLE {ident(table)} ADD COLUMN IF NOT EXISTS "
                         f"{column} {sql_type(value)}")
            log(f"{table}: new column {name} {sql_type(value)}")
            continue
        if (isinstance(value, float)
                and have[name] in ("bigint", "integer", "smallint", "numeric")):
            conn.execute(f"ALTER TABLE {ident(table)} ALTER COLUMN {column} "
                         f"TYPE DOUBLE PRECISION")
            log(f"{table}: widened {name} {have[name]} -> double precision",
                "WARN")


# ---------------------------------------------------------------------------
# Parsing: nextpnr
# ---------------------------------------------------------------------------
def last_run(text: str) -> str:
    """Only the last nextpnr run in the log.

    `top.tim` is nextpnr's `--log`, and it has been observed to accumulate across
    invocations. Reading the first block reports an older attempt as though it
    were this build -- the same class of mistake as configuring a stale
    bitstream, and just as invisible. `Logic utilisation before packing:` is
    printed exactly once per run, so it is the boundary.
    """
    marker = "Logic utilisation before packing:"
    return text[text.rindex(marker):] if marker in text else text


UTIL_RE = re.compile(r"^Info:\s+(\w+):\s+(\d+)/\s*(\d+)\s+(\d+)%", re.M)
PREPACK_RE = re.compile(r"^Info:\s+(Total LUT4s|logic LUTs|carry LUTs|RAM LUTs|"
                        r"RAMW LUTs|Total DFFs):\s+(\d+)/\s*(\d+)", re.M)
FMAX_RE = re.compile(r"Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz "
                     r"\((PASS|FAIL) at ([\d.]+) MHz\)")
CRIT_RE = re.compile(r"Critical path report for clock '([^']+)'")


def parse_nextpnr(path: Path) -> dict:
    """Utilisation and timing out of nextpnr's own log.

    Returns `{"cells": [...], "timing": [...], "headline": {...}}`. Nothing here
    is estimated or inferred from source: these are the tool's numbers for the
    design it actually placed.
    """
    if not path.exists():
        raise FileNotFoundError(f"no nextpnr log at {path}")
    text = last_run(path.read_text(errors="replace"))

    cells = [{"bel": bel, "used": int(used), "available": int(avail),
              "pct": float(pct)}
             for bel, used, avail, pct in UTIL_RE.findall(text)]

    # The pre-pack LUT4 split -- logic / carry / RAM / RAMW -- exists only in
    # this part of the log, and it is what tells a carry-chain regression from a
    # logic one. Named `prepack_*` so it cannot be confused with the bel counts.
    prepack = {}
    for name, used, avail in PREPACK_RE.findall(text):
        key = "prepack_" + (name.lower().replace("total ", "")
                            .replace(" ", "_"))
        prepack[key] = int(used)
        prepack[key + "_of"] = int(avail)

    by_bel = {c["bel"]: c for c in cells}

    def used(bel, default=0):
        return by_bel[bel]["used"] if bel in by_bel else default

    def pct(bel):
        return by_bel[bel]["pct"] if bel in by_bel else None

    headline = {
        # LUT4 is the pre-pack count -- what the design asks for -- and
        # TRELLIS_COMB is what the packer spent. They differ (14,289 against
        # 14,755 on the build this was written from) because LUT5-7 packing and
        # the distributed-RAM paths add cells. Recording only one of them is how
        # "the LUT count changed and nothing else did" becomes a puzzle.
        "lut4": prepack.get("prepack_lut4s"),
        "comb": used("TRELLIS_COMB"),
        "comb_pct": pct("TRELLIS_COMB"),
        "ff": used("TRELLIS_FF"),
        "ff_pct": pct("TRELLIS_FF"),
        "ramw": used("TRELLIS_RAMW"),
        "bram": used("DP16KD"),
        "bram_pct": pct("DP16KD"),
        "dsp": used("MULT18X18D"),
        "io": used("TRELLIS_IO"),
        "io_pct": pct("TRELLIS_IO"),
        "pll": used("EHXPLLL"),
        "dcc": used("DCCA"),
        "iologic": used("IOLOGIC"),
        "dqsbuf": used("DQSBUFM"),
    }
    headline.update(prepack)

    timing = {}
    for clock, achieved, verdict, constraint in FMAX_RE.findall(text):
        # Last block wins: nextpnr reports timing after placement AND after
        # routing, and only the second describes what the bitstream does.
        timing[clock] = {
            "clock": clock,
            "achieved_mhz": float(achieved),
            "constraint_mhz": float(constraint),
            "passed": verdict == "PASS",
            "slack_pct": ((float(achieved) - float(constraint))
                          / float(constraint) * 100.0),
            "path_from": None,
            "path_to": None,
        }

    # Where each critical path starts and ends. `BtbPlugin_logic_mem.0.0.DOA8`
    # as a start point is the difference between "the CPU got slower" and "the
    # branch predictor's block RAM read is now the whole cycle" -- which is
    # exactly the finding behind --relaxed-btb in cpu/cpu.py.
    for match in CRIT_RE.finditer(text):
        clock = match.group(1)
        if clock not in timing:
            continue
        block = text[match.end():]
        end = block.find("Critical path report")
        if end != -1:
            block = block[:end]
        sources = re.findall(r"Source (\S+)", block)
        sinks = re.findall(r"Sink (\S+)", block)
        if sources:
            timing[clock]["path_from"] = sources[0][:512]
        if sinks:
            timing[clock]["path_to"] = sinks[-1][:512]

    return {"cells": cells, "timing": list(timing.values()),
            "headline": headline}


# ---------------------------------------------------------------------------
# Parsing: where the block RAM went
# ---------------------------------------------------------------------------
# The rules that turn a flattened instance path into something a person can act
# on. First match wins, and the raw instance name is stored alongside, so a bad
# rule here costs a relabelling and never the underlying fact.
BRAM_GROUPS = [
    (r"^ram\.mem", "main block RAM (writable region)"),
    (r"FetchL1", "cpu i-cache"),
    (r"LsuL1", "cpu d-cache"),
    (r"Btb", "cpu btb"),
    (r"ila", "ila"),
    (r"^serial\.usb|usb", "usb buffers"),
    (r"^bootram|^flash", "flash / bootram"),
    (r"^cpu\.", "cpu (other)"),
]


def bram_group(instance: str) -> str:
    for pattern, name in BRAM_GROUPS:
        if re.search(pattern, instance, re.I):
            return name
    return "other"


def parse_bram_instances(path: Path) -> list[dict]:
    """Which instance owns each DP16KD, from the netlist nextpnr was handed.

    A total does not tell anybody that 32 of 44 blocks are the 64 KiB writable
    region and only 6 are the caches -- and that is the number a decision turns
    on, because shrinking the region is available and shrinking the caches costs
    the CPU.

    Scanned line by line rather than `json.load`ed: `top.json` is 28 MB and this
    runs on every build. Cell names in yosys JSON precede their `"type"`, so the
    most recent `"<name>": {` before a DP16KD type line is that cell's name.
    """
    if not path.exists():
        return []
    name_re = re.compile(r'^\s*"(.+)": \{\s*$')
    type_re = re.compile(r'^\s*"type": "([^"]+)"')
    counts: dict[str, int] = {}
    current = None
    with path.open(errors="replace") as handle:
        for line in handle:
            match = name_re.match(line)
            if match:
                current = match.group(1)
                continue
            kind = type_re.match(line)
            if kind and kind.group(1) == "DP16KD" and current:
                # `ram.mem.0.17` is one block of a 32-block memory, and the
                # memory is the unit anybody reasons about, so the trailing
                # bank/slice indices are folded away.
                instance = re.sub(r"\.\d+\.\d+$", "", current)
                counts[instance] = counts.get(instance, 0) + 1
    return [{"instance": name[:191], "grp": bram_group(name), "blocks": count}
            for name, count in sorted(counts.items())]


def parse_placed_bram(path: Path) -> int | None:
    """DP16KD blocks in the PLACED config -- the bitstream's own answer.

    Kept separate from the netlist count on purpose: they can differ, and when
    they do the difference belongs to the packer rather than to a parse error.
    Cheap: one pass over a 7 MB text file.
    """
    if not path.exists():
        return None
    pattern = re.compile(r"^enum: EBR\d+\.MODE DP16KD")
    with path.open(errors="replace") as handle:
        return sum(1 for line in handle if pattern.match(line))


# ---------------------------------------------------------------------------
# Configuration -- imported from the gateware, never restated
# ---------------------------------------------------------------------------
# Address-map and pin identity. Real constants, but not build knobs: they say
# where a peripheral lives, not what was built. `soc_generate_pac.py` already
# owns the memory map, and 40 base addresses in this table would bury the
# columns that actually move.
CONFIG_SKIP = re.compile(r"_BASE$|^IRQ_|^GPIO_|^ROOT$|^VEXII$|_OFFSET$")


def gateware_config() -> dict:
    """Every configuration knob, one key each, read from the modules themselves.

    Two sources:

      * module-level scalar constants of `gateware/soc/top.py` -- `SYNC_MHZ`,
        `CACHE_SETS`, `HYPERRAM_DQS`, `FLASH_PHY_FAST` and whatever lands next;
      * `GENERATE_FLAGS` in `gateware/soc/cpu/cpu.py`, split into one column per
        flag, so `--with-btb` is `btb`, `--relaxed-btb` is `relaxed_btb` and
        `--performance-counters 4` is `performance_counters = 4`.

    Discovered rather than listed, which is the only version that stays true: a
    hand-maintained list here would be missing the newest flag exactly when
    somebody asks what the newest flag cost.
    """
    sys.path.insert(0, str(ROOT / "gateware"))
    sys.path.insert(0, str(ROOT / "gateware" / "soc"))
    import top as soc_top                                     # noqa: PLC0415
    from cpu.cpu import GENERATE_FLAGS                        # noqa: PLC0415

    cfg: dict = {}
    for name, value in vars(soc_top).items():
        if not name.isupper() or CONFIG_SKIP.search(name):
            continue
        if isinstance(value, (bool, int, float, str)):
            cfg[name.lower()] = value

    index = 0
    while index < len(GENERATE_FLAGS):
        token = GENERATE_FLAGS[index]
        index += 1
        if not token.startswith("--"):
            continue
        key = token[2:]
        if key.startswith("with-"):
            key = key[len("with-"):]
        key = key.replace("-", "_")
        value: bool | int | str = True
        if (index < len(GENERATE_FLAGS)
                and not GENERATE_FLAGS[index].startswith("--")):
            raw = GENERATE_FLAGS[index]
            index += 1
            value = int(raw) if raw.lstrip("-").isdigit() else raw
        # A generator flag and a top.py constant could in principle collide.
        # Prefix rather than overwrite: losing one silently would make that
        # column meaningless with nothing saying so.
        cfg["cpu_" + key if key in cfg else key] = value
    return cfg


def config_hash(cfg: dict) -> str:
    """Identity of a configuration, for grouping a distribution.

    Two builds share a distribution only if every flag matched. That is the
    point: the spread of fmax is a property of one configuration under placement
    noise, and mixing two configurations into one population is how a real
    change gets attributed to noise, or noise to a change.
    """
    payload = json.dumps({k: cfg[k] for k in sorted(cfg)}, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Firmware and toolchain
# ---------------------------------------------------------------------------
def firmware_sizes() -> dict:
    """Section sizes from the ELF, plus the two images actually loaded."""
    elf = (ROOT / "firmware" / "cynthion-soc" / "target"
           / "riscv32imac-unknown-none-elf" / "release" / "cynthion-soc")
    out: dict = {}
    if elf.exists():
        result = subprocess.run(["riscv64-linux-gnu-size", "-A", str(elf)],
                                capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].startswith("."):
                    name = parts[0].lstrip(".").replace(".", "_")
                    if name in ("text", "rodata", "data", "bss", "init"):
                        out[f"fw_{name}_bytes"] = int(parts[1])
    for key, path in (("fw_bram_image_bytes", ROOT / "tmp" / "rust_fw.bin"),
                      ("fw_flash_image_bytes", ROOT / "tmp" / "rust_rodata.bin"),
                      ("fw_boot_bytes", ROOT / "tmp" / "rust_boot.bin")):
        if path.exists():
            out[key] = path.stat().st_size
    manifest = ROOT / "firmware" / "cynthion-soc" / "Cargo.toml"
    if manifest.exists():
        match = re.search(r'opt-level\s*=\s*"?(\w+)"?', manifest.read_text())
        if match:
            out["fw_opt_level"] = match.group(1)
    return out


def tool_version(argv: list[str],
                 pattern: str = r"(\d+\.\d+[\w.+-]*)") -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return "absent"
    lines = ((result.stdout or "") + (result.stderr or "")).splitlines()
    match = re.search(pattern, lines[0] if lines else "")
    return match.group(1) if match else (lines or ["unknown"])[0][:120]


def toolchain() -> dict:
    return {
        "yosys_version": tool_version(["yosys", "-V"]),
        "nextpnr_version": tool_version(["nextpnr-ecp5", "--version"]),
        "rustc_version": tool_version(["rustc", "--version"]),
        "python_version": sys.version.split()[0],
    }


def git_state() -> dict:
    def git(*args, default=""):
        result = subprocess.run(["git", *args], cwd=ROOT,
                                capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else default

    return {
        "git_ref": git("rev-parse", "HEAD", default="0" * 40),
        "git_short": git("rev-parse", "--short", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "subject": git("log", "-1", "--pretty=%s")[:255],
    }


def nextpnr_opts() -> str:
    """The place-and-route flags, because they change the answer.

    `--parallel-refine` trades a little fmax for a lot of wall clock and
    `--router router2` buys it back. A row that does not say which was used
    cannot honestly be compared with one that used the other.
    """
    try:
        from fast_build_env import NEXTPNR_OPTS               # noqa: PLC0415
        return NEXTPNR_OPTS
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Collecting one build
# ---------------------------------------------------------------------------
def collect(build_dir: Path = BUILD_DIR, *, target: str = DEFAULT_TARGET,
            status: str = "ok", build_seconds: float | None = None) -> dict:
    """Everything about the build sitting in `build_dir`, as one record."""
    pnr = parse_nextpnr(Path(build_dir) / "top.tim")
    cfg = gateware_config()
    row: dict = {
        # ISO 8601 with the offset, into a TIMESTAMPTZ. A local wall-clock
        # string would put the daylight-saving changeover into the series as an
        # hour where builds ran backwards.
        "built_at": datetime.now().astimezone()
        .isoformat(timespec="milliseconds"),
        "target": target,
        "status": status,
        "config_hash": config_hash(cfg),
        "host": socket.gethostname(),
        "build_seconds": float(build_seconds) if build_seconds else None,
    }
    row.update(git_state())
    row.update(toolchain())
    row["nextpnr_opts"] = nextpnr_opts()
    row.update({k: v for k, v in pnr["headline"].items() if v is not None})
    row.update(firmware_sizes())
    row.update(cfg)

    placed = parse_placed_bram(Path(build_dir) / "top.config")
    if placed is not None:
        row["bram_placed"] = placed
    bit = Path(build_dir) / "top.bit"
    if bit.exists():
        row["bitstream_bytes"] = bit.stat().st_size
        row["bitstream_sha256"] = hashlib.sha256(bit.read_bytes()).hexdigest()[:32]

    return {
        "build": row,
        "cells": pnr["cells"],
        "timing": pnr["timing"],
        "bram": parse_bram_instances(Path(build_dir) / "top.json"),
    }


# ---------------------------------------------------------------------------
# Writing, and the spool for when the server is down
# ---------------------------------------------------------------------------
def spool(record: dict) -> Path:
    SPOOL.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    path = SPOOL / f"{stamp}-{record['build'].get('git_short', 'nogit')}.json"
    path.write_text(json.dumps(record, indent=1, default=str))
    return path


def insert(conn, record: dict) -> int:
    """One build, four tables, one transaction. Returns the new `builds.id`.

    `RETURNING id` rather than a last-insert-id, so the identity comes back from
    the statement that made it and the three child inserts cannot possibly attach
    themselves to somebody else's row.
    """
    build = {k: v for k, v in record["build"].items() if v is not None}
    ensure_columns(conn, "builds", build)
    columns = ", ".join(ident(k) for k in build)
    marks = ", ".join(f":{k}" for k in build)
    build_id = conn.execute(
        f"INSERT INTO builds ({columns}) VALUES ({marks}) RETURNING id",
        build).scalar_one()
    for cell in record["cells"]:
        conn.execute("INSERT INTO build_cells (build_id, bel, used, available, "
                     "pct) VALUES (:build_id, :bel, :used, :available, :pct)",
                     {"build_id": build_id, **cell})
    for entry in record["timing"]:
        conn.execute("INSERT INTO build_timing (build_id, clock, "
                     "constraint_mhz, achieved_mhz, passed, slack_pct, "
                     "path_from, path_to) VALUES (:build_id, :clock, "
                     ":constraint_mhz, :achieved_mhz, :passed, :slack_pct, "
                     ":path_from, :path_to)", {"build_id": build_id, **entry})
    for block in record["bram"]:
        conn.execute("INSERT INTO build_bram (build_id, instance, grp, blocks) "
                     "VALUES (:build_id, :instance, :grp, :blocks)",
                     {"build_id": build_id, **block})
    return build_id


def store(record: dict) -> tuple[bool, str]:
    """Write it, or spool it and say so. Never a silent third destination."""
    try:
        conn = connect()
    except MetricsUnavailable as exc:
        path = spool(record)
        return False, f"{exc}\n\nSPOOLED: {path.relative_to(ROOT)}"
    try:
        flushed = flush_spool(conn)
        build_id = insert(conn, record)
        conn.commit()
    finally:
        conn.close()
    note = f" (and {flushed} spooled record(s) flushed)" if flushed else ""
    return True, f"recorded as build #{build_id}{note}"


def flush_spool(conn) -> int:
    """Replay the JSON records taken while the server was down.

    The files are deleted only after the transaction that carries them commits.
    Doing it the other way round -- unlink as you go -- means a rollback or a
    crash at the wrong moment destroys the only copy of a build that is now in no
    database, which is the exact hole the spool exists to prevent.
    """
    if not SPOOL.exists():
        return 0
    done = []
    for path in sorted(SPOOL.glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError:
            log(f"unreadable spool file left in place: {path}", "ERROR")
            continue
        insert(conn, record)
        done.append(path)
    if done:
        conn.commit()
        for path in done:
            path.unlink()
    return len(done)


def record_build(build_dir: Path = BUILD_DIR, *, target: str = DEFAULT_TARGET,
                 status: str = "ok",
                 build_seconds: float | None = None) -> bool:
    """The entry point the build calls. Loud on failure, never fatal.

    Called unconditionally by `soc_run.py`; there is no flag to turn it off, and
    that is the requirement -- opt-in metrics are metrics with a hole in them
    exactly where somebody was in a hurry.

    It does not fail the build, because a stopped database is not a reason to
    throw away a bitstream that took a minute to make. What it does instead is
    keep the record on disk and print the fix, and `./dev.py metrics status`
    stays non-zero until the spool is empty.
    """
    try:
        record = collect(build_dir, target=target, status=status,
                         build_seconds=build_seconds)
    except Exception as exc:                  # never take the build down
        emit(f"BUILD NOT RECORDED: could not read the build's own output: {exc}")
        return False
    ok, message = store(record)
    build = record["build"]
    if ok:
        emit(f"metrics: {message} -- {build.get('comb')} COMB, "
             f"{build.get('ff')} FF, {build.get('bram')} BRAM, "
             f"{len(record['timing'])} clock domain(s)")
    else:
        emit("*** BUILD NOT RECORDED IN POSTGRES ***")
        for line in message.splitlines():
            emit("    " + line)
    return ok


# ---------------------------------------------------------------------------
# The dry run, for a machine where the server cannot be started
# ---------------------------------------------------------------------------
class RecordingCursor:
    """What one recorded statement hands back, in place of a SQLAlchemy `Result`.

    The two shapes the callers below actually use: iteration, for the
    `information_schema` probe in `ensure_columns`, and `scalar_one`, for the
    `RETURNING id` in `insert`. Everything else answers empty, which is what a
    real `Result` for a DDL statement amounts to.
    """

    def __init__(self, rows: list):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def scalar_one(self):
        return self._rows[0][0]

    def mappings(self):
        raise NotImplementedError(
            "the dry run records the writes; there are no rows to read back")


class RecordingConnection:
    """A `Db` that records SQL instead of running it.

    Not a second database and not a fallback -- it writes nothing anywhere and
    connects to nothing. It exists so `./dev.py metrics sql` can show the exact
    statements a build would execute, on a machine where creating the database
    needs a privilege the person sitting there does not have. Reviewing the
    schema then does not depend on being able to run it, which on this machine is
    the difference between a reviewable schema and none.

    It drives the REAL `ensure_schema`, `ensure_columns` and `insert`, which is
    the point: a hand-written sample of the SQL would be a second implementation
    and would be the one that was wrong.
    """

    def __init__(self, existing: dict | None = None):
        self.log: list = []
        self.existing = existing or {}

    def execute(self, sql: str, params: dict | None = None) -> RecordingCursor:
        statement = " ".join(sql.split())
        self.log.append((statement, dict(params or {})))
        if statement.startswith("SELECT column_name"):
            return RecordingCursor(list(self.existing.items()))
        if statement.endswith("RETURNING id"):
            return RecordingCursor([(1,)])
        return RecordingCursor([])

    def commit(self):
        pass

    def close(self):
        pass


def cmd_sql(args) -> int:
    """Print the schema and the statements this build would run. Writes nothing."""
    record = collect(Path(args.build_dir))
    conn = RecordingConnection(core_columns())
    ensure_schema(conn)
    build_id = insert(conn, record)
    emit(f"-- PostgreSQL. The {len(CORE_SQL)} core statements, run on every "
         f"connection and idempotent:")
    for statement, _ in conn.log[:len(CORE_SQL)]:
        emit(statement + ";")
        emit("")
    adds = [t for t, _ in conn.log if t.startswith("ALTER TABLE")]
    emit(f"-- {len(adds)} columns added on first sight of this build "
         f"(the schema growing itself):")
    for statement in adds:
        emit(statement + ";")
    emit("")
    inserts = [(t, a) for t, a in conn.log if t.startswith("INSERT")]
    emit(f"-- {len(inserts)} INSERTs for build #{build_id}: 1 build, "
         f"{len(record['cells'])} cell types, {len(record['timing'])} clocks, "
         f"{len(record['bram'])} block RAM instances")
    first = inserts[0]
    emit(first[0][:400] + (" ..." if len(first[0]) > 400 else ""))
    emit(f"-- with {len(first[1])} bound parameters, none of them interpolated")
    return 0


def cmd_selftest(args) -> int:
    """Exercise collect + the whole write path without a database."""
    failures = []

    def check(label, cond):
        emit(f"  {'ok  ' if cond else 'FAIL'}  {label}")
        if not cond:
            failures.append(label)

    record = collect(Path(args.build_dir))
    build = record["build"]
    check("utilisation parsed from nextpnr", bool(build.get("comb")))
    check("block RAM total present", build.get("bram") is not None)
    check("placed config counted separately",
          build.get("bram_placed") is not None)
    check("per-instance block RAM breakdown", len(record["bram"]) > 1)
    check("breakdown sums to the total",
          sum(b["blocks"] for b in record["bram"]) == build.get("bram"))
    check("timing has at least one clock domain", len(record["timing"]) >= 1)
    check("every clock has a constraint and a verdict",
          all(t["constraint_mhz"] and t["passed"] in (True, False)
              for t in record["timing"]))
    check("critical path start point captured",
          any(t["path_from"] for t in record["timing"]))
    check("configuration columns present",
          all(k in build for k in ("sync_mhz", "cache_sets", "hyperram_dqs",
                                   "flash_phy_fast", "btb", "relaxed_btb",
                                   "performance_counters")))
    check("firmware sections recorded", "fw_text_bytes" in build)
    check("toolchain versions recorded",
          build.get("nextpnr_version", "absent") != "absent")

    conn = RecordingConnection(core_columns())
    ensure_schema(conn)
    insert(conn, record)
    statements = [t for t, _ in conn.log]
    added = {re.search(r'ADD COLUMN IF NOT EXISTS "([^"]+)"', t).group(1)
             for t in statements if "ADD COLUMN" in t}
    missing = ({k for k, v in build.items() if v is not None}
               - added - set(core_columns()))
    check(f"every one of the {len(build)} collected fields reaches a column",
          not missing)
    if missing:
        emit(f"        missing: {sorted(missing)}")
    check("one INSERT per row",
          sum(1 for t in statements if t.startswith("INSERT"))
          == 1 + len(record["cells"]) + len(record["timing"])
          + len(record["bram"]))

    # The dialect, checked on the statements themselves rather than trusted.
    # This started life against MySQL, and a half-finished port is the failure
    # that looks fine here and fails on the one machine that has the server.
    check("no MySQL left in the generated SQL",
          not [t for t in statements if "`" in t or "%s" in t
               or "AUTO_INCREMENT" in t])
    check("every value is a bound parameter, no literal reaches the SQL",
          not [t for t in statements if "'" in t])
    check("the self-growing add is ADD COLUMN IF NOT EXISTS",
          all("ADD COLUMN IF NOT EXISTS" in t
              for t in statements if "ADD COLUMN" in t))
    check("the build's id comes back from the INSERT itself",
          any(t.startswith("INSERT INTO builds") and t.endswith("RETURNING id")
              for t in statements))
    check("the drivers are the builds that keep the GIL off",
          drivers_ok())

    # The renderer and the statistics are pure functions of the rows, so they
    # can be checked with no server in sight -- which matters here, because the
    # server is exactly what is missing on this machine.
    svg = svg_chart("test", [("a", [1.0, 2.0, None, 4.0])],
                    ["r1", "r2", "r3", "r4"])
    check("svg chart renders a line and skips gaps",
          svg.startswith("<svg") and "polyline" in svg and svg.count("circle") == 3)
    spread = summarise([65.49, 72.70, 64.76])
    check("distribution reports spread, not a point",
          spread["n"] == 3 and abs(spread["sd"] - 4.29) < 0.1
          and spread["min"] == 64.76 and spread["max"] == 72.70)
    check("delta marks the direction that is worse",
          delta(100, 90).endswith("!)") and delta(90, 100).endswith("0)")
          and delta(90, 100, invert=True).endswith("!)"))

    if failures:
        emit(f"selftest FAILED: {len(failures)} check(s)")
        return 1
    emit("selftest ok (no database was touched)")
    return 0


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def fetch(conn, sql: str, params: dict | None = None) -> list[dict]:
    return [dict(row) for row in conn.execute(sql, params).mappings()]


def timing_for(conn, build_id: int) -> list[dict]:
    return fetch(conn, "SELECT * FROM build_timing WHERE build_id = :id "
                       "ORDER BY constraint_mhz DESC", {"id": build_id})


def bram_for(conn, build_id: int) -> list[dict]:
    return fetch(conn, "SELECT grp, SUM(blocks) AS blocks, COUNT(*) AS n "
                       "FROM build_bram WHERE build_id = :id GROUP BY grp "
                       "ORDER BY blocks DESC", {"id": build_id})


def branch_point_ref() -> str | None:
    result = subprocess.run(["git", "merge-base", "HEAD", "origin/main"],
                            cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def delta(now, then, *, digits=0, invert=False) -> str:
    """`now` against `then`, with the direction that means "worse" marked `!`.

    Area going up and frequency going down are both bad and they have opposite
    signs, so the direction is a parameter rather than a convention to remember.
    """
    if now is None or then is None:
        return ""
    change = float(now) - float(then)
    if abs(change) < 10 ** -(digits + 2):
        return "  (=)"
    worse = (change < 0) if invert else (change > 0)
    return f"  ({'+' if change > 0 else ''}{change:.{digits}f}" \
           f"{'!' if worse else ''})"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_status(args) -> int:
    pending = sorted(SPOOL.glob("*.json")) if SPOOL.exists() else []
    cfg = settings()
    emit(f"database : {cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}")
    emit(f"driver   : psycopg 3 (pure) + SQLAlchemy 2.0 Core (no C extension), "
         f"GIL {'ON' if getattr(sys, '_is_gil_enabled', lambda: True)() else 'off'}")
    try:
        conn = connect()
    except MetricsUnavailable as exc:
        emit("state    : UNUSABLE")
        emit("")
        for line in str(exc).splitlines():
            emit("  " + line)
        emit("")
        emit(f"spooled  : {len(pending)} record(s) waiting")
        return 1
    rows = fetch(conn, "SELECT COUNT(*) AS n, MIN(built_at) AS first, "
                       "MAX(built_at) AS last FROM builds")[0]
    columns = fetch(conn, "SELECT COUNT(*) AS n FROM information_schema.columns "
                          "WHERE table_schema = current_schema() AND "
                          "table_name = 'builds'")
    space = fetch(conn, "SELECT current_setting('default_tablespace') AS ts, "
                        "pg_tablespace_location((SELECT oid FROM pg_tablespace "
                        "WHERE spcname = current_setting('default_tablespace')))"
                        " AS location")
    emit("state    : up")
    emit(f"tablespc : {space[0]['ts']} at {space[0]['location']}")
    emit(f"builds   : {rows['n']}  ({rows['first']} .. {rows['last']})")
    emit(f"columns  : {columns[0]['n']} on builds")
    if pending:
        emit(f"spooled  : {len(pending)} record(s) NOT yet in the database -- "
             f"`./dev.py metrics flush`")
    conn.close()
    return 1 if pending else 0


def cmd_record(args) -> int:
    ok = record_build(Path(args.build_dir), target=args.target,
                      status=args.status, build_seconds=args.seconds)
    return 0 if ok else 1


def cmd_flush(args) -> int:
    try:
        conn = connect()
    except MetricsUnavailable as exc:
        emit(str(exc))
        return 1
    count = flush_spool(conn)
    conn.close()
    emit(f"flushed {count} spooled record(s)")
    return 0


def cmd_show(args) -> int:
    try:
        conn = connect()
    except MetricsUnavailable as exc:
        emit(str(exc))
        return 1
    rows = fetch(conn, "SELECT id, built_at, git_short, dirty, branch, status, "
                       "comb, ff, bram, config_hash FROM builds "
                       "ORDER BY id DESC LIMIT :limit", {"limit": args.limit})
    emit(f"{'id':>4}  {'built_at':<19} {'ref':<10} {'branch':<20} "
         f"{'COMB':>6} {'FF':>6} {'BRAM':>5}  cfg")
    for row in rows:
        ref = (row["git_short"] or "") + ("+" if row["dirty"] else "")
        # A TIMESTAMPTZ comes back as an aware datetime, not a string. Rendered
        # to the second and in local time: the column is for recognising a build
        # in a list, and the offset is the same on every row.
        when = row["built_at"].astimezone().strftime("%Y-%m-%d %H:%M:%S")
        emit(f"{row['id']:>4}  {when:<19} {ref:<10} "
             f"{(row['branch'] or '')[:20]:<20} "
             f"{row['comb'] or 0:>6.0f} {row['ff'] or 0:>6.0f} "
             f"{row['bram'] or 0:>5.0f}  {row['config_hash'][:8]}")
    conn.close()
    return 0


def summarise(values) -> dict:
    values = [float(v) for v in values if v is not None]
    if not values:
        return {}
    return {
        "n": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "sd": statistics.stdev(values) if len(values) > 1 else 0.0,
        "max": max(values),
    }


def cmd_report(args) -> int:
    try:
        conn = connect()
    except MetricsUnavailable as exc:
        emit(str(exc))
        return 1
    builds = fetch(conn, "SELECT * FROM builds WHERE target = :target "
                         "ORDER BY id DESC LIMIT 2", {"target": args.target})
    if not builds:
        emit(f"no builds recorded yet for target {args.target!r} -- "
             f"run `./dev.py build`")
        conn.close()
        return 1
    current = builds[0]
    prev = builds[1] if len(builds) > 1 else None

    same = fetch(conn, "SELECT * FROM builds WHERE target = :target AND "
                       "config_hash = :config AND status = 'ok' "
                       "ORDER BY id DESC LIMIT :window",
                 {"target": args.target, "config": current["config_hash"],
                  "window": args.window})
    best_rows = fetch(conn, "SELECT * FROM builds WHERE target = :target AND "
                            "status = 'ok' AND comb IS NOT NULL "
                            "ORDER BY comb ASC LIMIT 1", {"target": args.target})
    base_ref = branch_point_ref()
    base_rows = fetch(conn, "SELECT * FROM builds WHERE target = :target AND "
                            "git_ref = :ref ORDER BY id DESC LIMIT 1",
                      {"target": args.target,
                       "ref": base_ref}) if base_ref else []
    best = best_rows[0] if best_rows else None
    bp = base_rows[0] if base_rows else None

    def col(row, key):
        return row.get(key) if row else None

    emit("=" * 78)
    emit(f"BUILD #{current['id']}  {current['built_at']}  "
         f"{current['git_short']}{'+dirty' if current['dirty'] else ''}  "
         f"[{current['branch']}]  {current['status']}")
    emit(f"config {current['config_hash']}   "
         f"{current.get('build_seconds') or 0:.0f} s   "
         f"nextpnr {current.get('nextpnr_version')}, "
         f"yosys {current.get('yosys_version')}")
    emit("=" * 78)

    emit("")
    emit("AREA                current    vs prev     vs best    vs branch point")
    for key, label in (("comb", "TRELLIS_COMB"), ("lut4", "LUT4 (pre-pack)"),
                       ("ff", "TRELLIS_FF"), ("bram", "DP16KD"),
                       ("dsp", "MULT18X18D"), ("io", "TRELLIS_IO"),
                       ("pll", "EHXPLLL")):
        value = current.get(key)
        if value is None:
            continue
        emit(f"  {label:<18}{value:>8}"
             f"{delta(value, col(prev, key)):<12}"
             f"{delta(value, col(best, key)):<11}"
             f"{delta(value, col(bp, key))}")
    if bp is None:
        emit(f"  (no build recorded at the branch point "
             f"{(base_ref or 'unknown')[:12]})")

    emit("")
    emit("TIMING (this build)")
    now_timing = timing_for(conn, current["id"])
    prev_timing = {t["clock"]: t
                   for t in (timing_for(conn, prev["id"]) if prev else [])}
    for entry in now_timing:
        mark = "PASS" if entry["passed"] else "FAIL"
        old = prev_timing.get(entry["clock"], {}).get("achieved_mhz")
        emit(f"  {entry['clock'][:34]:<34} {entry['achieved_mhz']:>7.2f} MHz "
             f"vs {entry['constraint_mhz']:>6.2f} constraint  {mark}"
             f"{delta(entry['achieved_mhz'], old, digits=2, invert=True)}")
        if entry["path_from"]:
            emit(f"      from {entry['path_from'][:64]}")

    emit("")
    emit(f"DISTRIBUTION over the {len(same)} build(s) sharing config "
         f"{current['config_hash']}")
    if len(same) < 2:
        emit("  ONE build. There is no distribution yet, and the fmax above is")
        emit("  a property of this placement rather than of the design -- which")
        emit("  is the mistake this table exists to stop. Build it again.")
    else:
        ids = [row["id"] for row in same]
        # `= ANY(:ids)` rather than an IN list built from N placeholders: the
        # array goes in as one bound parameter, so the statement text does not
        # change with the size of the window and nothing is interpolated.
        rows = fetch(conn, "SELECT clock, achieved_mhz FROM build_timing "
                           "WHERE build_id = ANY(:ids)", {"ids": ids})
        per_clock: dict = {}
        for row in rows:
            per_clock.setdefault(row["clock"], []).append(row["achieved_mhz"])
        emit(f"  {'clock':<34} {'n':>3} {'min':>8} {'median':>8} {'mean':>8} "
             f"{'sd':>6} {'max':>8}")
        for clock, values in sorted(per_clock.items()):
            s = summarise(values)
            emit(f"  {clock[:34]:<34} {s['n']:>3} {s['min']:>8.2f} "
                 f"{s['median']:>8.2f} {s['mean']:>8.2f} {s['sd']:>6.2f} "
                 f"{s['max']:>8.2f}")
        for key in ("comb", "ff", "bram"):
            s = summarise([row.get(key) for row in same])
            if s:
                emit(f"  {key:<34} {s['n']:>3} {s['min']:>8.0f} "
                     f"{s['median']:>8.0f} {s['mean']:>8.0f} {s['sd']:>6.1f} "
                     f"{s['max']:>8.0f}")

    emit("")
    emit("WHERE THE BLOCK RAM WENT")
    total = current.get("bram") or 0
    for row in bram_for(conn, current["id"]):
        share = 100.0 * float(row["blocks"]) / total if total else 0.0
        emit(f"  {row['grp']:<38} {int(row['blocks']):>3} blocks "
             f"({share:4.1f}%)  {int(row['n'])} instance(s)")
    if current.get("bram_placed") is not None:
        emit(f"  placed config reports {current['bram_placed']} EBR in DP16KD "
             f"mode against {total} in the netlist")

    conn.close()
    return 0


# ---------------------------------------------------------------------------
# Graphs -- SVG written here, because no plotting library is installed on this
# interpreter and a trend line is forty lines of arithmetic
# ---------------------------------------------------------------------------
def svg_chart(title: str, series, labels, *, unit: str = "",
              width: int = 900, height: int = 300) -> str:
    colours = ["#2b7cd3", "#d35f2b", "#2ba05a", "#8a4fd3", "#b03060", "#777"]
    pad_l, pad_r, pad_t, pad_b = 64, 160, 34, 46
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    values = [v for _, ys in series for v in ys if v is not None]
    if not values:
        return f"<p>{title}: no data</p>"
    lo, hi = min(values), max(values)
    if hi == lo:
        hi = lo + 1
    span = hi - lo
    lo, hi = lo - span * 0.1, hi + span * 0.1
    count = max(len(labels), 2)

    def x(i):
        return pad_l + plot_w * i / (count - 1)

    def y(v):
        return pad_t + plot_h * (1 - (v - lo) / (hi - lo))

    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
           f'font-family="ui-monospace,monospace" font-size="11">',
           f'<text x="{pad_l}" y="18" font-size="13" font-weight="600">'
           f'{title}</text>']
    for step in range(5):
        value = lo + (hi - lo) * step / 4
        yy = y(value)
        out.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{pad_l + plot_w}" '
                   f'y2="{yy:.1f}" stroke="#ddd"/>')
        out.append(f'<text x="{pad_l - 6}" y="{yy + 3:.1f}" text-anchor="end" '
                   f'fill="#666">{value:.4g}{unit}</text>')
    for index, label in enumerate(labels):
        if count > 12 and index % max(1, count // 10):
            continue
        out.append(f'<text x="{x(index):.1f}" y="{height - pad_b + 14:.0f}" '
                   f'text-anchor="middle" fill="#666" '
                   f'transform="rotate(25 {x(index):.1f} '
                   f'{height - pad_b + 14:.0f})">{label}</text>')
    for number, (name, ys) in enumerate(series):
        colour = colours[number % len(colours)]
        points = [f"{x(i):.1f},{y(v):.1f}"
                  for i, v in enumerate(ys) if v is not None]
        if points:
            out.append(f'<polyline fill="none" stroke="{colour}" '
                       f'stroke-width="2" points="{" ".join(points)}"/>')
            for i, v in enumerate(ys):
                if v is not None:
                    out.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" '
                               f'r="2.5" fill="{colour}"/>')
        out.append(f'<text x="{pad_l + plot_w + 10}" '
                   f'y="{pad_t + 14 + number * 15}" fill="{colour}">'
                   f'{name}</text>')
    out.append("</svg>")
    return "\n".join(out)


def cmd_graph(args) -> int:
    try:
        conn = connect()
    except MetricsUnavailable as exc:
        emit(str(exc))
        return 1
    rows = fetch(conn, "SELECT * FROM builds WHERE target = :target "
                       "ORDER BY id ASC", {"target": args.target})
    if not rows:
        emit("nothing to plot: no builds recorded yet")
        conn.close()
        return 1
    labels = [f"{r['git_short'] or '?'}{'+' if r['dirty'] else ''} "
              f"{str(r['built_at'])[5:16]}" for r in rows]
    ids = [r["id"] for r in rows]
    timing = fetch(conn, "SELECT build_id, clock, achieved_mhz, "
                         "constraint_mhz FROM build_timing "
                         "WHERE build_id = ANY(:ids)", {"ids": ids})
    conn.close()

    achieved: dict = {}
    constrained: dict = {}
    for row in timing:
        achieved.setdefault(row["clock"], {})[row["build_id"]] = \
            row["achieved_mhz"]
        constrained.setdefault(row["clock"], {})[row["build_id"]] = \
            row["constraint_mhz"]

    charts = [
        svg_chart("Area: logic", [
            ("TRELLIS_COMB", [r.get("comb") for r in rows]),
            ("TRELLIS_FF", [r.get("ff") for r in rows]),
        ], labels),
        svg_chart("Area: block RAM, DSP, IO", [
            ("DP16KD", [r.get("bram") for r in rows]),
            ("MULT18X18D", [r.get("dsp") for r in rows]),
            ("TRELLIS_IO", [r.get("io") for r in rows]),
        ], labels),
        svg_chart("fmax by clock domain (dashed pairs are the constraint)", [
            *[(clock.replace("$glbnet$", ""), [seen.get(i) for i in ids])
              for clock, seen in sorted(achieved.items())],
            *[(clock.replace("$glbnet$", "") + " target",
               [seen.get(i) for i in ids])
              for clock, seen in sorted(constrained.items())],
        ], labels, unit=" MHz"),
        svg_chart("Build time", [
            ("seconds", [r.get("build_seconds") for r in rows]),
        ], labels, unit=" s"),
    ]

    OUTDIR.mkdir(parents=True, exist_ok=True)
    page = OUTDIR / "trends.html"
    page.write_text(
        "<!doctype html><meta charset='utf-8'>"
        "<title>cynthion-workspace build metrics</title>"
        "<style>body{font-family:ui-sans-serif,system-ui;margin:2rem;"
        "max-width:1100px}svg{border:1px solid #eee;margin:1rem 0}</style>"
        f"<h1>Build metrics: {len(rows)} build(s)</h1>"
        f"<p>Generated "
        f"{datetime.now().astimezone().isoformat(timespec='seconds')} "
        f"from target <code>{args.target}</code>.</p>"
        + "\n".join(charts))
    emit(f"wrote {page.relative_to(ROOT)}  ({len(rows)} builds)")
    emit(f"  code-insiders {page}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("## Why")[0].strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("status", help="is the server up, is anything spooled")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("record", help="record the build in tmp/vexii_hello/build")
    p.add_argument("--build-dir", default=str(BUILD_DIR))
    p.add_argument("--target", default=DEFAULT_TARGET)
    p.add_argument("--status", default="ok")
    p.add_argument("--seconds", type=float, default=None)
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("report",
                       help="current vs previous vs best vs branch point")
    p.add_argument("--target", default=DEFAULT_TARGET)
    p.add_argument("--window", type=int, default=DISTRIBUTION_WINDOW)
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("graph", help="trend SVGs into tmp/build-metrics/")
    p.add_argument("--target", default=DEFAULT_TARGET)
    p.set_defaults(fn=cmd_graph)

    p = sub.add_parser("flush", help="push spooled records into the database")
    p.set_defaults(fn=cmd_flush)

    p = sub.add_parser("show", help="the last N builds, one line each")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("sql", help="the exact SQL a build runs; touches nothing")
    p.add_argument("--build-dir", default=str(BUILD_DIR))
    p.set_defaults(fn=cmd_sql)

    p = sub.add_parser("selftest", help="parse + write path, without a database")
    p.add_argument("--build-dir", default=str(BUILD_DIR))
    p.set_defaults(fn=cmd_selftest)

    p = sub.add_parser("collect", help="print the record as JSON, write nothing")
    p.add_argument("--build-dir", default=str(BUILD_DIR))
    p.set_defaults(fn=lambda a: (print(json.dumps(
        collect(Path(a.build_dir)), indent=2, default=str)), 0)[1])

    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
