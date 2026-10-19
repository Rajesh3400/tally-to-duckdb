"""tally-to-duckdb command line."""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import signal
import sys
import threading

from tally_to_duckdb import __version__, jobs, tieout
from tally_to_duckdb import db as dbmod
from tally_to_duckdb.config import get_settings


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _sources(d: dbmod.Db, name: str | None) -> list[dict]:
    q = "SELECT * FROM sources" + (" WHERE tally_company_name = ?" if name else "") + " ORDER BY books_from, id"
    rows = d.all(q, [name] if name else None)
    if name and not rows:
        sys.exit(f'No source named "{name}". See list-sources.')
    return rows


def cmd_init(a, d):
    print(f"Database ready: {get_settings().db_path}")


def cmd_add_source(a, d):
    if d.scalar("SELECT count(*) FROM sources WHERE tally_company_name = ?", [a.company]):
        sys.exit(f'Source "{a.company}" already exists.')
    cid = a.entity_id or d.scalar("SELECT coalesce(max(company_id), 0) + 1 FROM sources")
    sid = d.scalar("INSERT INTO sources (company_id, tally_company_name, books_from) VALUES (?, ?, ?) RETURNING id",
                   [cid, a.company, a.books_from])
    print(f'Added source {sid}: "{a.company}" (entity {cid}, books from {a.books_from}).')


def cmd_list_sources(a, d):
    for s in _sources(d, None):
        print(f'{s["id"]:>3}  entity {s["company_id"]}  books from {s["books_from"]}  last voucher {s["last_voucher_date"]}  '
              f'last sync {s["last_sync_status"] or "never"}  "{s["tally_company_name"]}"')


def cmd_sync(a, d):
    jobs.mark_abandoned(d)
    cancel = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: (print("\nCancelling after the current Tally request..."), cancel.set()))
    failed = 0
    for s in _sources(d, a.company):
        print(f'== {s["tally_company_name"]}')
        last = [""]

        def progress(msg, done, total):
            if msg != last[0]:
                print(f"   [{done}/{total}] {msg}".replace("…", "..."))
                last[0] = msg
        status, message, _ = jobs.run(d, s["id"], a.full, progress, cancel)
        print(f"   {status}: {message}")
        failed += status not in ("SUCCESS", "NO_CHANGE")
        if cancel.is_set():
            break
    sys.exit(1 if failed else 0)


def cmd_tieout(a, d):
    bad = 0
    for s in _sources(d, a.company):
        v = tieout.check_source(d, s["id"])
        print(f'== {s["tally_company_name"]}: {"OK" if v.ok else "NOT OK"}\n{v.detail}')
        bad += not v.ok
    sys.exit(1 if bad else 0)


def cmd_status(a, d):
    print(f"tally-to-duckdb {__version__}  db: {get_settings().db_path}")
    cmd_list_sources(a, d)
    for r in d.all("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 5"):
        print(f'run {r["id"]} source {r["source_id"]} {r["kind"]} {r["status"]} {r["started_at"]}  {(r["message"] or "")[:80]}')


def main(argv=None) -> None:
    # the options work before or after the command (SUPPRESS: an unset one must not overwrite the other)
    common = argparse.ArgumentParser(add_help=False)
    S = argparse.SUPPRESS
    common.add_argument("--db", default=S, help="DuckDB file (env T2D_DB)")
    common.add_argument("--host", default=S, help="Tally host (env T2D_TALLY_HOST)")
    common.add_argument("--port", type=int, default=S, help="Tally XML port (env T2D_TALLY_PORT)")
    common.add_argument("--memory-limit", default=S, help="DuckDB memory limit (env T2D_MEMORY_LIMIT)")
    common.add_argument("--threads", type=int, default=S, help="DuckDB threads (env T2D_THREADS)")
    common.add_argument("-v", "--verbose", action="store_true", default=S)
    p = argparse.ArgumentParser(prog="tally-to-duckdb", description="Tally Prime -> DuckDB sync", parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", parents=[common], help="create the database and schema").set_defaults(f=cmd_init)
    s = sub.add_parser("add-source", parents=[common], help="register a Tally company file")
    s.add_argument("--company", required=True, help="company name exactly as Tally shows it")
    s.add_argument("--books-from", required=True, type=_date, help="YYYY-MM-DD (replaced by Tally's value on first sync)")
    s.add_argument("--entity-id", type=int, help="join an existing entity (yearly files of one business share it)")
    s.set_defaults(f=cmd_add_source)
    sub.add_parser("list-sources", parents=[common]).set_defaults(f=cmd_list_sources)
    s = sub.add_parser("sync", parents=[common], help="sync one source (--company) or all")
    s.add_argument("--company")
    s.add_argument("--full", action="store_true")
    s.set_defaults(f=cmd_sync)
    s = sub.add_parser("tieout", parents=[common], help="compare our numbers with Tally's closing balances")
    s.add_argument("--company")
    s.set_defaults(f=cmd_tieout)
    sub.add_parser("status", parents=[common]).set_defaults(f=cmd_status)
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO if getattr(a, "verbose", False) else logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    st = get_settings()
    for opt, attr in (("db", "db_path"), ("host", "tally_host"), ("port", "tally_port"),
                      ("memory_limit", "duckdb_memory_limit"), ("threads", "duckdb_threads")):
        if hasattr(a, opt):
            setattr(st, attr, getattr(a, opt))
    with dbmod.session() as d:
        a.f(a, d)
    dbmod.close()


if __name__ == "__main__":
    main()
