"""Tally -> DuckDB for ONE source (Tally company file). Follows the Tally
Database Loader's sync (see NOTICE), with three deliberate improvements:

  1. Everything is fetched into a scratch schema first; only when Tally has
     answered every request is anything changed, in ONE transaction. A failed
     or cancelled sync changes nothing.
  2. AlterID watermarks are written in that same transaction, with the data.
  3. Cancel = a flag checked between Tally requests.

Raw rows go into the shared tally.* tables tagged source_id, and the same transaction also replaces Tally's closing figures,
rebuilds the company's derived tables (derive.py) and runs the tie-out
(tieout.py) - so the cube can never disagree with the postings, and every
sync ends with a verdict against Tally's own numbers.

Row ORDER is preserved (DuckDB keeps insertion order); stock FIFO's same-day
order depends on it (stock FIFO downstream depends on it).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable

from tally_to_duckdb.config import get_settings
from tally_to_duckdb.db import Db
from tally_to_duckdb import client as tc
from tally_to_duckdb import derive, tieout

Progress = Callable[[str, int, int], None]
STAGE = "_stage"
log = logging.getLogger("app.sync")


class SyncCancelled(Exception):
    pass


class SyncIntegrityError(Exception):
    """The fetched data failed a consistency check - nothing was written."""


@dataclass
class SyncResult:
    status: str                     # SUCCESS / NO_CHANGE
    rows_loaded: dict[str, int] = field(default_factory=dict)
    tie_out_ok: bool | None = None
    tie_out_detail: str = ""


def connection_of(db: Db | None = None) -> tc.TallyConn:
    s = get_settings()
    return tc.TallyConn(s.tally_host, s.tally_port, s.tally_protocol, s.tally_user, s.tally_password)


@contextlib.contextmanager
def _tsv_relation(db: Db, tsv: str, cols: list[str]):
    """The TSV as relation `_incoming`, read from a temporary FILE - never
    io.StringIO: DuckDB serves Python file objects through ONE object store per
    database, and a sync that failed mid-way left its relation registered, so
    every later sync failed with "DUCKDB_INTERNAL_OBJECTSTORE has already been
    registered" (seen on a long-running server)."""
    tmp_dir = Path(get_settings().db_path).parent / "sync_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".tsv", dir=tmp_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(tsv)
        rel = db.cur.read_csv(path, sep="\t", header=True, quotechar="", escapechar="",
                              columns={c: "VARCHAR" for c in cols}, na_values=[tc.NULL_MARK])
        db.cur.register("_incoming", rel)
        try:
            yield
        finally:
            db.cur.unregister("_incoming")
            del rel
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


def _column_types(db: Db, table: str) -> dict[str, str]:
    return dict(db.rows("SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema = 'tally' AND table_name = ? AND column_name <> 'source_id'", [table]))


def _stage(db: Db, table: str, tsv: str) -> int:
    """Rows -> _stage.<table>, typed like tally.<table>. Loader's NULL rule:
    the 'ñ' sentinel is NULL, an empty text stays ''."""
    types = _column_types(db, table)
    header = tsv.split("\r\n", 1)[0].split("\t")
    cols = [c for c in header if c in types]  # a definition column the table lacks is ignored (loader did too)
    exprs = [f'"{c}"' if types[c].startswith("VARCHAR") else f'CAST(NULLIF("{c}", \'\') AS {types[c]}) AS "{c}"' for c in cols]
    db.run(f'CREATE TABLE IF NOT EXISTS {STAGE}."{table}" AS SELECT * EXCLUDE (source_id) FROM tally."{table}" LIMIT 0')
    with _tsv_relation(db, tsv, header):
        return db.scalar(f'INSERT INTO {STAGE}."{table}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) '
                         f'SELECT {", ".join(exprs)} FROM _incoming')


def _stage_plain(db: Db, name: str, tsv: str, cols: list[str]) -> None:
    with _tsv_relation(db, tsv, cols):
        db.run(f'CREATE OR REPLACE TABLE {STAGE}."{name}" AS SELECT * FROM _incoming')


ATTEMPTS = 4          # per Tally request; a short or cut-off answer is asked again


def _rows(tsv: str) -> int:
    return sum(1 for line in tsv.split("\r\n")[1:] if line.strip())


def _guids(tsv: str) -> set[str]:
    return {line.split("\t", 1)[0] for line in tsv.split("\r\n")[1:] if line.strip()}


def _col(tsv: str, name: str) -> int:
    return tsv.split("\r\n", 1)[0].split("\t").index(name)


def _accounting_guids(tsv: str, t: dict) -> set[str]:
    k = _col(tsv, "is_accounting_voucher")
    return {p[0] for line in tsv.split("\r\n")[1:] if line.strip() and (p := line.split("\t"))[k].strip() == "1"}


def _ask(fn: Callable[[], str], what: str, check: Callable[[], None], judge: Callable[[str], str | None] | None = None) -> str:
    """One Tally request, re-asked when the answer is cut off or fails `judge`
    (which returns a problem or None). This Tally returned EMPTY and SHORT
    answers to the same request under load, so no single answer
    is trusted. Gives up after ATTEMPTS with SyncIntegrityError - nothing is
    written then."""
    problem = ""
    for attempt in range(ATTEMPTS):
        check()
        try:
            tsv = fn()
        except tc.TallyIncomplete as exc:
            problem = str(exc)
        else:
            problem = judge(tsv) if judge else None
            if not problem:
                return tsv
        log.warning("Tally answer rejected (%s, attempt %d/%d): %s", what, attempt + 1, ATTEMPTS, problem)
        if attempt + 1 < ATTEMPTS:
            time.sleep(min(30, 3 * 2 ** attempt))
    raise SyncIntegrityError(f"{what}: {problem}. Tally gave a wrong answer {ATTEMPTS} times - nothing was changed. "
                             "Try again when Tally is less busy.")


def _stable_probe(conn, company: str, period: tuple, extra: list[str] | None, check: Callable[[], None],
                  expect_last: dt.date | None = None) -> tc.Windows:
    """The voucher count everything is checked against: asked until two
    answers agree exactly (count, windows and per-window counts).

    Agreement alone is NOT proof: a Tally short of memory (up for
    many hours, nearly out of memory) returned the SAME short list twice (about a
    third of the vouchers, nothing after a mid-year date). So an unfiltered list must also reach `expect_last` - Tally's
    own LastVoucherDate from company_info, a separate request a cut-off list
    cannot fake."""
    last = None
    for attempt in range(ATTEMPTS + 1):
        check()
        try:
            w = tc.date_windows(conn, company, *period, extra)
        except tc.TallyIncomplete:
            continue
        if expect_last is not None and w.last_date != expect_last:
            log.warning("Tally's voucher list ends %s but its last voucher is dated %s - incomplete, asking again",
                        w.last_date, expect_last)
            last = None
            if attempt < ATTEMPTS:
                time.sleep(min(30, 3 * 2 ** attempt))
            continue
        if last is not None and (w.vouchers, w.windows, w.counts) == (last.vouchers, last.windows, last.counts):
            return w
        if last is not None:
            log.warning("Tally voucher counts disagree (%s vs %s) - asking again", last.vouchers, w.vouchers)
        last = w
    raise SyncIntegrityError("Tally did not give a complete, consistent voucher list (it gave different counts, or a list "
                             "ending before its own last voucher date) - nothing was changed. Tally may be short of memory: "
                             "restart Tally, open the company, and sync again.")


def _judge_window(t: dict, tsv: str, i: int, windowed: bool, batched: bool, probe: tc.Windows, seen: set[str],
                  window_vouchers: list[set[str]], window_accounting: list[set[str]]) -> str | None:
    """Is this answer complete and right for its window? None = accept."""
    if not windowed:
        return None
    here = _guids(tsv)
    if here & seen:
        return f"{len(here & seen):,} voucher(s) already fetched by an earlier batch (Tally ignored the date window)"
    if t["name"] == "trn_voucher":
        want = probe.counts[i] if batched else probe.vouchers
        got = _rows(tsv)
        return None if got == want else f"{got:,} vouchers, Tally counted {want:,}"
    if i >= len(window_vouchers):
        return None
    outside = here - window_vouchers[i]
    if outside:
        return f"{len(outside):,} voucher(s) not in this batch's voucher list"
    if t["name"] == "trn_accounting":
        missing = window_accounting[i] - here
        if missing:
            return f"{len(missing):,} accounting voucher(s) came back without their lines"
        k = _col(tsv, "amount")
        sums: dict[str, Decimal] = {}
        for line in tsv.split("\r\n")[1:]:
            if line.strip():
                p = line.split("\t")
                if p[0] in window_accounting[i]:
                    sums[p[0]] = sums.get(p[0], Decimal(0)) + Decimal(p[k].strip() or "0")
        unbalanced = sum(1 for v in sums.values() if v != 0)
        if unbalanced:
            return f"{unbalanced:,} accounting voucher(s) whose lines do not net to zero (answer cut mid-voucher)"
    return None


def _verify(db: Db, schema: str, source_id: int | None, masters: list[dict], transactions: list[dict],
            loaded: set[str], expected_vouchers: int | None, where: str) -> None:
    """The consistency rules a pull must satisfy; any failure raises
    SyncIntegrityError. Run on the STAGED rows (schema _stage, source_id None)
    before anything is written, and on tally.* for the source inside the write
    transaction (a failure there rolls the whole sync back).

      1. A Primary table (masters with a guid, trn_voucher) holds each guid
         once - a repeat means the same object was fetched twice.
      2. trn_voucher holds exactly the vouchers Tally counted (the date probe,
         same filters) - none lost, none extra.
      3. Every row of a Derived transaction table belongs to a voucher in
         trn_voucher - nothing orphaned.
    """
    src = "" if source_id is None else f" AND source_id = {int(source_id)}"
    problems = []
    for t in masters + transactions:
        if t.get("nature") != "Primary" or t["name"] not in loaded:
            continue
        dup = db.scalar(f'SELECT count(*) - count(DISTINCT guid) FROM {schema}."{t["name"]}" WHERE TRUE{src}')
        if dup:
            problems.append(f"{t['name']}: {dup:,} repeated row(s)")
    if expected_vouchers is not None and "trn_voucher" in loaded:
        n = db.scalar(f"SELECT count(*) FROM {schema}.trn_voucher WHERE TRUE{src}")
        if n != expected_vouchers:
            problems.append(f"trn_voucher: {n:,} vouchers but Tally counted {expected_vouchers:,}")
    if "trn_voucher" in loaded:
        same = "" if source_id is None else " AND v.source_id = d.source_id"
        for t in transactions:
            if t.get("nature") == "Primary" or t["name"] not in loaded:
                continue
            orphans = db.scalar(f'SELECT count(*) FROM {schema}."{t["name"]}" d WHERE TRUE{src.replace("source_id", "d.source_id")} '
                                f"AND NOT EXISTS (SELECT 1 FROM {schema}.trn_voucher v WHERE v.guid = d.guid{same})")
            if orphans:
                problems.append(f"{t['name']}: {orphans:,} row(s) of vouchers that were not fetched")
    if source_id is not None:
        # Final tables only: references a full company must satisfy (0
        # exceptions in over a million real lines). A cut-off MASTER answer shows here.
        for tbl, col, master in (("trn_accounting", "_ledger", "mst_ledger"), ("trn_cost_centre", "_costcentre", "mst_cost_centre")):
            missing = db.scalar(f"""SELECT count(DISTINCT d.{col}) FROM tally.{tbl} d WHERE d.source_id = ? AND d.{col} IS NOT NULL
                                    AND NOT EXISTS (SELECT 1 FROM tally.{master} m WHERE m.source_id = d.source_id AND m.guid = d.{col})""",
                                [source_id])
            if missing:
                problems.append(f"{tbl}: {missing:,} {master[4:]}(s) used but missing from the masters")
        closings, ledgers = (db.scalar("SELECT count(*) FROM tally_closing WHERE source_id = ? AND kind = 'LEDGER'", [source_id]),
                             db.scalar("SELECT count(*) FROM tally.mst_ledger WHERE source_id = ?", [source_id]))
        if closings != ledgers:
            problems.append(f"{closings:,} closing balances for {ledgers:,} ledgers")
    if problems:
        raise SyncIntegrityError(f"Sync stopped {where} - nothing was changed: " + "; ".join(problems))


def sync_source(db: Db, source_id: int, full: bool, progress: Progress, cancel: threading.Event) -> SyncResult:
    def check():
        if cancel.is_set():
            raise SyncCancelled()

    src = db.one("SELECT * FROM sources WHERE id = ?", [source_id])
    company = src["tally_company_name"]
    conn = connection_of(db)
    masters, transactions = tc.load_definition()
    all_tables = masters + transactions
    total = len(all_tables) + 2  # + closings + write
    progress("Connecting to Tally…", 0, total)
    tc.set_company(conn, company)
    info = tc.company_info(conn, company)
    period = (info.books_from, info.last_voucher_date)

    has_data = db.scalar("SELECT count(*) FROM tally.trn_voucher WHERE source_id = ?", [source_id]) > 0
    last_master, last_trn = src["alterid_master"], src["alterid_transaction"]
    # AlterIDs only ever rise in a live company. LOWER than our watermark means
    # Tally now holds an OLDER copy (restored from a backup). Incremental would
    # then delete what Tally no longer has and fetch nothing below the old
    # watermark - leaving changed vouchers simply missing. Seen in practice:
    # a restored company left a few hundred ledgers off. Full sync.
    went_back = info.alterid_master < last_master or info.alterid_transaction < last_trn
    if went_back and not full:
        progress("Tally holds an older copy of this company than the last sync - running a FULL sync instead…", 0, total)
    full = full or went_back or not has_data or not src["alterid_master"]
    master_changed = full or info.alterid_master != last_master
    trn_changed = full or info.alterid_transaction != last_trn
    if not master_changed and not trn_changed:
        return SyncResult("NO_CHANGE")

    db.run(f"DROP SCHEMA IF EXISTS {STAGE} CASCADE")
    db.run(f"CREATE SCHEMA {STAGE}")
    rows_loaded: dict[str, int] = {}
    deletes: dict[str, str] = {}   # table -> SQL selecting the guids to delete (incremental)
    done = 0
    try:
        check()
        # The probe sees the same vouchers the pull will (same filters, plus the
        # AlterID one on an incremental). Its count is the anchor of every check,
        # so it is asked until two answers AGREE (Tally gave short answers under
        # load).
        probe = (_stable_probe(conn, company, period, None if full else [f"$AlterID > {last_trn}"], check,
                               info.last_voucher_date if full else None)
                 if trn_changed else tc.Windows([], 0, []))
        windows = probe.windows
        if full and trn_changed:
            # A full sync REPLACES the stored data. Far fewer vouchers than we
            # hold means a damaged answer far more often than a real mass
            # deletion - stop and say so rather than replace good data.
            held = db.scalar("SELECT count(DISTINCT guid) FROM tally.trn_voucher WHERE source_id = ?", [source_id]) or 0
            if held and probe.vouchers < 0.9 * held:
                raise SyncIntegrityError(
                    f"Tally now reports {probe.vouchers:,} vouchers for this company; {held:,} are already stored. A full "
                    "sync would replace them, so it was stopped - nothing was changed. If vouchers really were deleted "
                    "in Tally, ask an administrator; otherwise restart Tally and sync again.")

        # ---- incremental: which rows vanished or changed (loader tally.mjs) ----
        if not full:
            primaries = [t for t in masters if master_changed and t.get("nature") == "Primary"] + \
                        [t for t in transactions if trn_changed and t.get("nature") == "Primary"]
            for t in primaries:
                check()
                progress(f"Checking {t['name']} for changes…", done, total)
                diff = {"collection": t["collection"], "filters": t.get("filters"), "fetch": ["AlterId"],
                        "fields": [{"name": "guid", "field": "Guid", "type": "text"},
                                   {"name": "alterid", "field": "AlterId", "type": "text"}]}
                expect = None
                if t["name"] == "trn_voucher":
                    # The complete voucher list: every voucher Tally has, counted twice.
                    total_now = _stable_probe(conn, company, period, None, check, info.last_voucher_date).vouchers
                    expect = lambda tsv, n=total_now: (None if _rows(tsv) == n else f"{_rows(tsv):,} of {n:,} vouchers")  # noqa: E731
                tsv = _ask(lambda: tc.fetch(conn, diff, company, *period), f"{t['name']} change list", check, expect)
                _stage_plain(db, f"_diff_{t['name']}", tsv, ["guid", "alterid"])
                n = t["name"]
                deletes[n] = (
                    f'SELECT guid FROM tally."{n}" WHERE source_id = {source_id} AND guid NOT IN (SELECT guid FROM {STAGE}."_diff_{n}") '
                    f'UNION SELECT x.guid FROM tally."{n}" x JOIN {STAGE}."_diff_{n}" d ON d.guid = x.guid '
                    f"WHERE x.source_id = {source_id} AND CAST(d.alterid AS VARCHAR) <> CAST(x.alterid AS VARCHAR)")

        # Per window, what the voucher pull found: every voucher, and the
        # accounting ones - the yardstick for every other transaction table.
        window_vouchers: list[set[str]] = []
        window_accounting: list[set[str]] = []

        def fetch_table(t: dict, extra_filter: str | None, windowed: bool) -> None:
            nonlocal done
            base = list(t.get("filters") or []) + ([extra_filter] if extra_filter else [])
            batched = windowed and len(windows) > 1
            parts = windows if batched else [period]
            n = 0
            seen: set[str] = set()   # voucher guids already fetched for this table
            for i, (lo, hi) in enumerate(parts):
                check()
                progress(f"Fetching {t['name']} from Tally" + (f" (batch {i + 1}/{len(parts)})" if batched else "") + "…",
                         done, total)
                spec = dict(t)
                spec["filters"] = base + ([tc.window_filter(lo, hi)] if batched else [])

                def judge(tsv: str, i: int = i) -> str | None:
                    return _judge_window(t, tsv, i, windowed, batched, probe, seen, window_vouchers, window_accounting)

                tsv = _ask(lambda: tc.fetch(conn, spec, company, lo, hi),
                           f"{t['name']} batch {i + 1}/{len(parts)} ({lo}..{hi})", check, judge)
                here = _guids(tsv)
                if windowed and t["name"] == "trn_voucher":
                    window_vouchers.append(here)
                    window_accounting.append(_accounting_guids(tsv, t))
                seen |= here
                n += _stage(db, t["name"], tsv)
            rows_loaded[t["name"]] = rows_loaded.get(t["name"], 0) + n
            done += 1

        for t in masters:
            if master_changed:
                fetch_table(t, None if full else f"$AlterID > {last_master}", windowed=False)
            else:
                done += 1
        for t in transactions:
            if trn_changed:
                fetch_table(t, None if full else f"$AlterID > {last_trn}", windowed=True)
            else:
                done += 1

        refresh_numbers = False
        if not full and trn_changed and db.scalar(
                "SELECT count(*) FROM tally.mst_vouchertype WHERE source_id = ? AND numbering_method LIKE '%Auto%'", [source_id]):
            # Automatic numbering shifts every later voucher's number when one is
            # inserted in between - refresh them all (loader tally.mjs).
            trn_voucher = next(t for t in transactions if t["name"] == "trn_voucher")
            spec = {"collection": trn_voucher["collection"],
                    "filters": list(trn_voucher.get("filters") or []) +
                               ['$$IsEqual:($NumberingMethod:VoucherType:$VoucherTypeName):"Automatic"'],
                    "fields": [{"name": "guid", "field": "Guid", "type": "text"},
                               {"name": "voucher_number", "field": "VoucherNumber", "type": "text"}]}
            check()
            progress("Refreshing automatic voucher numbers…", done, total)
            _stage_plain(db, "_vchnumber", _ask(lambda: tc.fetch(conn, spec, company, *period), "voucher numbers", check),
                         ["guid", "voucher_number"])
            refresh_numbers = True

        check()
        progress("Fetching Tally's closing balances…", done, total)
        _stage_plain(db, "_closing", _ask(lambda: tc.closings(conn, company, *period), "closing balances", check),
                     ["kind", "guid", "closing"])
        done += 1
        check()

        progress("Checking the fetched data…", done, total)
        _verify(db, STAGE, None, masters, transactions, set(rows_loaded),
                probe.vouchers if trn_changed else None, "after fetching")
        # What tally.trn_voucher must hold once written: every voucher Tally has
        # now - the probe for a full pull; for an incremental one, the complete
        # guid list its deletion check fetched.
        final_vouchers = (probe.vouchers if full else
                          db.scalar(f"SELECT count(*) FROM {STAGE}._diff_trn_voucher") if trn_changed else None)

        # ---- apply: ONE transaction ----
        progress("Writing to the database…", done, total)
        with db.tx():
            if full:
                for t in all_tables:
                    db.run(f'DELETE FROM tally."{t["name"]}" WHERE source_id = ?', [source_id])
            else:
                for t in all_tables:
                    sql = deletes.get(t["name"])
                    if sql is None:
                        continue
                    db.run(f"CREATE OR REPLACE TEMP TABLE _gone AS {sql}")
                    db.run(f'DELETE FROM tally."{t["name"]}" WHERE source_id = ? AND guid IN (SELECT guid FROM _gone)', [source_id])
                    for cd in t.get("cascade_delete") or []:
                        db.run(f'DELETE FROM tally."{cd["table"]}" WHERE source_id = ? AND "{cd["field"]}" IN (SELECT guid FROM _gone)',
                               [source_id])
            for name in rows_loaded:
                db.run(f'INSERT INTO tally."{name}" SELECT ? AS source_id, * FROM {STAGE}."{name}"', [source_id])
            if not full and master_changed:
                # A renamed master must rename every reference (loader tally.mjs).
                for t in masters:
                    if t.get("nature") != "Primary":
                        continue
                    for cu in t.get("cascade_update") or []:
                        db.run(f'UPDATE tally."{cu["table"]}" AS x SET "{cu["field"]}" = s.name FROM tally."{t["name"]}" AS s '
                               f'WHERE x.source_id = ? AND s.source_id = x.source_id AND s.guid = x."_{cu["field"]}"', [source_id])
            if refresh_numbers:
                db.run(f"UPDATE tally.trn_voucher AS x SET voucher_number = s.voucher_number FROM {STAGE}._vchnumber AS s "
                       "WHERE x.source_id = ? AND s.guid = x.guid", [source_id])
            db.run("DELETE FROM tally_closing WHERE source_id = ?", [source_id])
            db.run(f"INSERT INTO tally_closing SELECT ?, kind, guid, CAST(closing AS DECIMAL(17, 2)) FROM {STAGE}._closing",
                   [source_id])
            db.run("UPDATE sources SET books_from = ?, last_voucher_date = ?, alterid_master = ?, alterid_transaction = ? "
                   "WHERE id = ?", [info.books_from, info.last_voucher_date, info.alterid_master, info.alterid_transaction, source_id])
            # The same rules on the final tables: a broken merge (incremental)
            # rolls everything back here, before the reports are rebuilt.
            _verify(db, "tally", source_id, masters, transactions, {t["name"] for t in all_tables},
                    final_vouchers, "while writing")
            derive.rebuild_company(db, src["company_id"])
    finally:
        db.run(f"DROP SCHEMA IF EXISTS {STAGE} CASCADE")

    # The tie-out runs AFTER the commit. It only reads, so it needs no
    # transaction - and inside one it ran out of memory (600MB) on a large company:
    # the uncommitted old + new raw rows and the rebuilt fact_line were all
    # held in memory beside its joins (after a very long fetch).
    verdict = tieout.check_source(db, source_id)
    db.run("UPDATE sources SET tie_out_ok = ?, tie_out_detail = ? WHERE id = ?", [verdict.ok, verdict.detail, source_id])
    return SyncResult("SUCCESS", rows_loaded, verdict.ok, verdict.detail)


def rows_json(rows: dict[str, int]) -> str:
    return json.dumps(rows, separators=(",", ":"))
