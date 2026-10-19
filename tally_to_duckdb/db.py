"""The whole data-access layer: one DuckDB file, plain SQL, no ORM.

- One duckdb connection per PROCESS (`connection()`); DuckDB takes an exclusive
  file lock, so only one process may open the file at a time.
- Each job gets its own cursor wrapped in `Db`, in autocommit mode: every
  statement sees the latest committed data. Writes that must be atomic go in
  `with db.tx():`.
- DuckDB rejects a concurrent second writer on the same row ("Conflict on
  update"), so run one sync at a time.

SCHEMA = ordered files schema/NNN_name.sql, applied once each and recorded in
schema_version by `apply_schema()`. Never edit a file that has shipped; add the
next number.
"""
from __future__ import annotations

import re
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# DuckDB's execute() tries `import pandas` ~16x per query to sniff DataFrame
# parameters; pandas is not installed and failed imports are not cached, so
# each try searched sys.path on disk (seconds per request). A None entry makes the import fail instantly.
# Do not install pandas without removing this.
sys.modules.setdefault("pandas", None)

import duckdb  # noqa: E402

from tally_to_duckdb.config import data_dir, get_settings  # noqa: E402

SCHEMA_DIR = data_dir("schema")

_con: duckdb.DuckDBPyConnection | None = None
_con_lock = threading.Lock()


def connect(path: str) -> duckdb.DuckDBPyConnection:
    s = get_settings()
    tmp = Path(path).parent / "duckdb_tmp" if path != ":memory:" else None
    cfg: dict[str, Any] = {"memory_limit": s.duckdb_memory_limit, "threads": s.duckdb_threads}
    if tmp:
        cfg["temp_directory"] = str(tmp)
    return duckdb.connect(path, config=cfg)


def connection() -> duckdb.DuckDBPyConnection:
    """The process-wide connection, opened (and the schema applied) on first use."""
    global _con
    with _con_lock:
        if _con is None:
            con = connect(get_settings().db_path)
            apply_schema(con)
            _con = con
        return _con


def close() -> None:
    """Closes the process connection (tests, shutdown). Next use reopens."""
    global _con
    with _con_lock:
        if _con is not None:
            _con.close()
            _con = None


class Db:
    """A cursor with four verbs. Rows come back as dicts (small result sets);
    for big ones use `rows()` (tuples) or `.cur` directly."""

    def __init__(self, cur: duckdb.DuckDBPyConnection):
        self.cur = cur
        self._depth = 0

    def run(self, sql: str, params: Any = None) -> None:
        self.cur.execute(sql, params) if params is not None else self.cur.execute(sql)

    def rows(self, sql: str, params: Any = None) -> list[tuple]:
        self.run(sql, params)
        return self.cur.fetchall()

    def all(self, sql: str, params: Any = None) -> list[dict]:
        self.run(sql, params)
        names = [d[0] for d in self.cur.description]
        return [dict(zip(names, r)) for r in self.cur.fetchall()]

    def one(self, sql: str, params: Any = None) -> dict | None:
        out = self.all(sql, params)
        return out[0] if out else None

    def scalar(self, sql: str, params: Any = None) -> Any:
        self.run(sql, params)
        r = self.cur.fetchone()
        return r[0] if r else None

    @contextmanager
    def tx(self) -> Iterator["Db"]:
        """A transaction. Nested use joins the outer one (no savepoints in DuckDB)."""
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return
        self.cur.begin()
        self._depth = 1
        try:
            yield self
            self.cur.commit()
        except BaseException:
            self.cur.rollback()
            raise
        finally:
            self._depth = 0

    def close(self) -> None:
        self.cur.close()


@contextmanager
def session() -> Iterator[Db]:
    """A cursor for a script or job."""
    db = Db(connection().cursor())
    try:
        yield db
    finally:
        db.close()


_FILE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def schema_files() -> list[tuple[int, Path]]:
    out = []
    for p in sorted(SCHEMA_DIR.glob("*.sql")):
        m = _FILE.match(p.name)
        if not m:
            raise RuntimeError(f"schema file {p.name} must be named NNN_lowercase_name.sql")
        out.append((int(m.group(1)), p))
    nums = [n for n, _ in out]
    if nums != list(range(1, len(nums) + 1)):
        raise RuntimeError(f"schema files must be numbered 001.. with no gaps, found {nums}")
    return out


def apply_schema(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Applies every schema file not yet recorded, each in its own transaction
    (DDL is transactional in DuckDB, so a failing file leaves nothing behind).
    Returns the names applied."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (n INTEGER PRIMARY KEY, name VARCHAR NOT NULL, "
        "applied_at TIMESTAMP NOT NULL DEFAULT current_timestamp)"
    )
    done = {r[0] for r in con.execute("SELECT n FROM schema_version").fetchall()}
    applied = []
    for n, path in schema_files():
        if n in done:
            continue
        con.begin()
        try:
            con.execute(path.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_version (n, name) VALUES (?, ?)", [n, path.name])
            con.commit()
        except BaseException:
            con.rollback()
            raise
        applied.append(path.name)
    return applied
