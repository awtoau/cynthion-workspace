#!/usr/bin/env python3
#
# The build recorder must emit PostgreSQL, and must emit it with no server here.
# SPDX-License-Identifier: BSD-3-Clause

"""`build_metrics.py` generates Postgres, and binds every value. (#232)

This started against MySQL and was ported, and a half-finished port is the kind
of change that passes every check on the machine without a database and fails on
the one machine that has it. So the statements are asserted here rather than
eyeballed: backticks, `%s`, `AUTO_INCREMENT` and `LIMIT %s` are all things that
parse fine as Python and are rejected by Postgres.

Nothing here connects to anything. `RecordingConnection` is the real write path
with the server replaced, so what is asserted is what a build would actually run
-- not a second copy of the SQL written for the test, which would be the copy
that stayed correct while the real one rotted.

The interpreter check is not decoration either: `psycopg[binary]` and a
wheel-installed SQLAlchemy both re-pin the GIL for the whole process, silently,
which would serialise the workspace's parallel build path. It has no error
message of its own, so this is the error message.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_metrics as bm  # noqa: E402


# One synthetic build, so this needs no gateware output on disk. The shapes are
# the ones `collect()` produces; the values are arbitrary.
RECORD = {
    "build": {
        "built_at": "2026-08-07T12:00:00.000+10:00",
        "target": "soc",
        "status": "ok",
        "git_ref": "0" * 40,
        "git_short": "abc1234",
        "branch": "soc-clocks",
        "dirty": True,
        "config_hash": "0123456789abcdef",
        "build_seconds": 91.5,
        "host": "test",
        # Not core: these are the self-growing part.
        "comb": 14755,
        "comb_pct": 60.4,
        "btb": True,
        "flash_mode": "qspi",
    },
    "cells": [{"bel": "TRELLIS_COMB", "used": 14755, "available": 24288,
               "pct": 60.0}],
    "timing": [{"clock": "$glbnet$sync", "achieved_mhz": 72.7,
                "constraint_mhz": 60.0, "passed": True, "slack_pct": 21.2,
                "path_from": "a", "path_to": "b"}],
    "bram": [{"instance": "ram.mem", "grp": "main block RAM (writable region)",
              "blocks": 32}],
}


@pytest.fixture(scope="module")
def statements():
    """Every statement a build would run, recorded instead of executed."""
    conn = bm.RecordingConnection(bm.core_columns())
    bm.ensure_schema(conn)
    bm.insert(conn, RECORD)
    return conn.log


def test_no_mysql_survives_the_port(statements):
    offenders = [sql for sql, _ in statements
                 if "`" in sql or "%s" in sql or "AUTO_INCREMENT" in sql
                 or "TINYINT" in sql or "DATETIME" in sql]
    assert not offenders, f"MySQL dialect left in: {offenders}"


def test_every_value_is_bound_not_interpolated(statements):
    """No literal reaches the SQL text -- values travel as parameters."""
    for sql, params in statements:
        assert "'" not in sql, f"literal in statement: {sql}"
        for name in re.findall(r":(\w+)", sql):
            assert name in params, f"{name} is unbound in: {sql}"


def test_the_schema_grows_itself_idempotently(statements):
    """A new flag becomes a column, and re-adding it is not an error."""
    adds = [sql for sql, _ in statements if "ADD COLUMN" in sql]
    assert adds, "nothing was added; the self-growing schema did nothing"
    assert all("ADD COLUMN IF NOT EXISTS" in sql for sql in adds)
    added = {re.search(r'ADD COLUMN IF NOT EXISTS "(\w+)"', sql).group(1)
             for sql in adds}
    # The four non-core fields in RECORD, and only those: the core columns are
    # already declared in CREATE TABLE and must not be added again.
    assert added == {"comb", "comb_pct", "btb", "flash_mode"}


def test_types_are_postgres_types():
    assert bm.sql_type(True) == "BOOLEAN"
    assert bm.sql_type(7) == "BIGINT"
    assert bm.sql_type(7.5) == "DOUBLE PRECISION"
    assert bm.sql_type("x" * 4096) == "TEXT"


def test_core_columns_speak_the_catalogue_vocabulary():
    """The parsed DDL types must match what information_schema reports back.

    `ensure_columns` compares one against the other, so "DOUBLE PRECISION" has
    to arrive as "double precision" and TIMESTAMPTZ as "timestamp with time
    zone". If it does not, the dry run reports adds that a real server would not
    make, and stops describing what happens.
    """
    core = bm.core_columns()
    assert core["built_at"] == "timestamp with time zone"
    assert core["build_seconds"] == "double precision"
    assert core["dirty"] == "boolean"
    assert core["branch"] == "text"


def test_build_id_comes_from_the_insert_itself(statements):
    inserts = [sql for sql, _ in statements if sql.startswith("INSERT INTO builds")]
    assert len(inserts) == 1
    assert inserts[0].endswith("RETURNING id")


def test_one_insert_per_row(statements):
    assert sum(1 for sql, _ in statements if sql.startswith("INSERT")) == 4


def test_identifiers_are_validated_not_trusted():
    assert bm.ident("hyperram_bist") == '"hyperram_bist"'
    for bad in ('drop"', "Upper", "1st", "has space", "x" * 64, ""):
        with pytest.raises(ValueError):
            bm.ident(bad)


def test_no_silent_fallback_to_another_store(monkeypatch):
    """A missing database raises with the recipe; it never opens something else."""
    monkeypatch.setenv("CYNTHION_METRICS_DATABASE", "cynthion_metrics_absent")
    with pytest.raises(bm.MetricsUnavailable) as caught:
        bm.connect()
    message = str(caught.value)
    # It raises rather than returning a handle to somewhere else, and what it
    # raises is the recipe -- including the tablespace, without which the data
    # would land on the nearly-full system drive.
    assert "sudo -u postgres psql -c \"CREATE TABLESPACE" in message
    assert "ALTER DATABASE" in message and "default_tablespace" in message
    assert "spooled" in message


def test_the_recipe_names_no_machines_path_when_unconfigured(monkeypatch):
    """With no machine config, the recipe is a placeholder and says why.

    This repository is public and `private_path_check.py` rejects a mount point
    in a tracked file, so the shared tablespace parent is configuration rather
    than a constant. The failure mode to avoid is a placeholder that reads like
    a real path, so the message has to say it is not one.
    """
    monkeypatch.setenv("CYNTHION_METRICS_TABLESPACE_ROOT", "")
    message = bm.how_to_create(bm.settings(), "because")
    assert bm.UNKNOWN_TABLESPACE_ROOT in message
    assert "NOBODY HAS TOLD THIS MACHINE WHERE ITS DATA DRIVE IS" in message


def test_drivers_keep_the_gil_off():
    """Pure psycopg, SQLAlchemy without its C extension, GIL still off."""
    bm.check_drivers()
    import psycopg
    from sqlalchemy.util import has_compiled_ext
    assert psycopg.pq.__impl__ == "python"
    assert not has_compiled_ext()
    assert not sys._is_gil_enabled()
