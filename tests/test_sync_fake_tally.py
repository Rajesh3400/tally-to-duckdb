"""The sync against a stand-in Tally - the code path that once cost a very long
full sync (Tally ignored the date windows; every voucher was stored several times).
The fake answers tally_to_duckdb.client.fetch from an
in-memory company and applies the filters the real requests carry: the
AlterID filter and the numeric date window. `honour_windows=False` behaves as
that Tally did.
"""
import datetime as dt
import re
import threading
from decimal import Decimal

import pytest

from tally_to_duckdb import client as tc
from tally_to_duckdb import sync
from tally_to_duckdb.tieout import check_source

D = dt.date


class FakeTally:
    def __init__(self):
        self.honour_windows = True
        self.ledgers = {"L1": {"guid": "L1", "alterid": 1, "name": "Sales", "opening": Decimal(0)},
                        "L2": {"guid": "L2", "alterid": 2, "name": "Cash", "opening": Decimal(-500)},
                        "L3": {"guid": "L3", "alterid": 3, "name": "Capital", "opening": Decimal(500)}}
        self.vouchers = {}   # guid -> {date, alterid, lines: [(ledger guid, amount)]}
        self.alter = 100
        for day in range(1, 10):
            self.add(f"v{day}", D(2026, 4, day), Decimal(10 * day))

    def add(self, guid, date, amount):
        self.alter += 1
        self.vouchers[guid] = {"date": date, "alterid": self.alter, "lines": [("L1", amount), ("L2", -amount)]}

    def change(self, guid, amount):
        self.alter += 1
        v = self.vouchers[guid]
        v["alterid"] = self.alter
        v["lines"] = [("L1", amount), ("L2", -amount)]

    def info(self, conn, company):
        return tc.CompanyInfo(company, D(2026, 4, 1), max(v["date"] for v in self.vouchers.values()),
                              max(l["alterid"] for l in self.ledgers.values()), self.alter)

    def fetch(self, conn, table, company, lo, hi):
        filters = table.get("filters") or []
        after = next((int(m.group(1)) for f in filters if (m := re.search(r"\$AlterID > (\d+)", f))), None)
        window = next(((int(m.group(1)), int(m.group(2))) for f in filters
                       if (m := re.search(r"&gt;= (\d{8}) AND .*&lt;= (\d{8})", f))), None)
        names = [f["name"] for f in table["fields"]]
        coll = table["collection"]
        rows: list[dict] = []
        if coll == "Voucher" or coll.startswith("Voucher."):
            for guid, v in self.vouchers.items():
                if after is not None and v["alterid"] <= after:
                    continue
                if window and self.honour_windows and not (window[0] <= int(v["date"].strftime("%Y%m%d")) <= window[1]):
                    continue
                if coll == "Voucher":
                    rows.append({"guid": guid, "alterid": v["alterid"], "date": v["date"].isoformat(), "is_accounting_voucher": 1,
                                 "voucher_type": "Journal", "voucher_number": guid})
                elif coll == "Voucher.AllLedgerEntries":
                    rows += [{"guid": guid, "ledger": self.ledgers[l]["name"], "_ledger": l, "amount": a} for l, a in v["lines"]]
        elif coll == "Ledger":
            for l in self.ledgers.values():
                if after is not None and l["alterid"] <= after:
                    continue
                closing = l["opening"] + sum((a for v in self.vouchers.values() for g, a in v["lines"] if g == l["guid"]), Decimal(0))
                rows.append({"guid": l["guid"], "alterid": l["alterid"], "name": l["name"], "opening_balance": l["opening"],
                             "closing": closing, "is_revenue": 0, "is_deemedpositive": 1})
        return "\t".join(names) + "".join("\r\n" + "\t".join(str(r.get(n, "")) for n in names) for r in rows)


@pytest.fixture
def tally(db, monkeypatch):
    fake = FakeTally()
    monkeypatch.setattr(tc, "fetch", fake.fetch)
    monkeypatch.setattr(tc, "company_info", fake.info)
    monkeypatch.setattr(tc, "set_company", lambda *a: None)
    monkeypatch.setattr(tc, "BATCH_SIZE", 3)          # 9 vouchers -> 3 windows
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)   # no real pauses between retries
    cid = 1
    sid = db.scalar("INSERT INTO sources (company_id, tally_company_name) VALUES (?, 'T 26') RETURNING id", [cid])
    return fake, sid


def run(db, sid, full=False):
    return sync.sync_source(db, sid, full, lambda *a: None, threading.Event())


def state(db, sid):
    vouchers = db.rows("SELECT guid, count(*) FROM tally.trn_voucher WHERE source_id = ? GROUP BY 1 ORDER BY 1", [sid])
    lines = dict(db.rows("SELECT guid || '/' || _ledger, amount FROM tally.trn_accounting WHERE source_id = ?", [sid]))
    return vouchers, lines


def expected(fake):
    return (sorted((g, 1) for g in fake.vouchers),
            {f"{g}/{l}": a for g, v in fake.vouchers.items() for l, a in v["lines"]})


def test_full_sync_in_windows_stores_each_voucher_once_and_ties_out(db, tally):
    fake, sid = tally
    result = run(db, sid, full=True)
    assert result.status == "SUCCESS" and result.tie_out_ok, result.tie_out_detail
    assert state(db, sid) == expected(fake)


def test_a_tally_that_ignores_the_windows_stops_the_sync_and_writes_nothing(db, tally):
    fake, sid = tally
    fake.honour_windows = False
    # The first window already brings back all 9 vouchers where Tally counted 3.
    with pytest.raises(sync.SyncIntegrityError, match="9 vouchers, Tally counted 3"):
        run(db, sid, full=True)
    assert db.scalar("SELECT count(*) FROM tally.trn_voucher") == 0
    assert db.scalar("SELECT alterid_transaction FROM sources WHERE id = ?", [sid]) == 0   # watermark untouched


def test_incremental_after_change_delete_and_add_equals_tally(db, tally, monkeypatch):
    fake, sid = tally
    run(db, sid, full=True)
    fake.change("v5", Decimal("55.55"))                 # altered
    del fake.vouchers["v2"]                             # deleted in Tally
    fake.add("v10", D(2026, 4, 10), Decimal(7))         # new
    fake.add("v0", D(2026, 4, 1), Decimal(3))           # new, back-dated into the first window
    monkeypatch.setattr(tc, "BATCH_SIZE", 1)            # force several windows for the 3 changed vouchers
    result = run(db, sid)
    assert result.status == "SUCCESS" and result.tie_out_ok, result.tie_out_detail
    assert state(db, sid) == expected(fake)
    assert run(db, sid).status == "NO_CHANGE"


def test_a_fetch_missing_vouchers_is_refused(db, tally, monkeypatch):
    """The voucher count check: a batch Tally counted 3 that brings 2 is re-asked, then refused."""
    fake, sid = tally
    real = fake.fetch

    def lossy(conn, table, company, lo, hi):
        out = real(conn, table, company, lo, hi)
        if table["collection"] == "Voucher" and len(table["fields"]) > 2:       # the real trn_voucher pull, not the probe
            out = "\r\n".join(l for l in out.split("\r\n") if not l.startswith("v4\t"))
        return out

    monkeypatch.setattr(tc, "fetch", lossy)
    with pytest.raises(sync.SyncIntegrityError, match="2 vouchers, Tally counted 3"):
        run(db, sid, full=True)
    assert db.scalar("SELECT count(*) FROM tally.trn_voucher") == 0


def test_an_empty_answer_once_is_asked_again_and_the_sync_succeeds(db, tally, monkeypatch):
    """The real Tally returned EMPTY batches under load."""
    fake, sid = tally
    real = fake.fetch
    calls = {"n": 0}
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)

    def flaky(conn, table, company, lo, hi):
        out = real(conn, table, company, lo, hi)
        is_batch = any("&gt;=" in f for f in table.get("filters") or [])
        if table["collection"] == "Voucher" and is_batch and lo.day >= 4 and calls["n"] == 0:
            calls["n"] += 1
            return out.split("\r\n", 1)[0]          # header only: an empty answer
        return out

    monkeypatch.setattr(tc, "fetch", flaky)
    result = run(db, sid, full=True)
    assert calls["n"] == 1 and result.status == "SUCCESS" and result.tie_out_ok
    assert state(db, sid) == expected(fake)


def test_a_voucher_cut_off_mid_way_is_refused(db, tally, monkeypatch):
    """An answer cut in the middle of a voucher leaves its lines not netting to zero."""
    fake, sid = tally
    real = fake.fetch
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)

    def cut(conn, table, company, lo, hi):
        out = real(conn, table, company, lo, hi)
        if table["collection"] == "Voucher.AllLedgerEntries":
            out = "\r\n".join(l for l in out.split("\r\n") if not (l.startswith("v5\t") and "\tL2\t" in l))
        return out

    monkeypatch.setattr(tc, "fetch", cut)
    with pytest.raises(sync.SyncIntegrityError, match="do not net to zero"):
        run(db, sid, full=True)
    assert db.scalar("SELECT count(*) FROM tally.trn_voucher") == 0


def test_a_tally_whose_voucher_count_keeps_changing_is_refused(db, tally, monkeypatch):
    fake, sid = tally
    real = fake.fetch
    n = {"i": 0}
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)

    def wobbly(conn, table, company, lo, hi):
        out = real(conn, table, company, lo, hi)
        if table["collection"] == "Voucher" and [f["name"] for f in table["fields"]] == ["date"]:   # the count probe
            n["i"] += 1
            out = "\r\n".join(out.split("\r\n")[: -(n["i"] % 3)] or out.split("\r\n")[:1])          # a different count each time
        return out

    monkeypatch.setattr(tc, "fetch", wobbly)
    with pytest.raises(sync.SyncIntegrityError, match="complete, consistent voucher list"):
        run(db, sid, full=True)


def test_the_same_short_list_twice_is_still_refused(db, tally, monkeypatch):
    """A memory-starved Tally returned the SAME cut list twice (a third of the
    vouchers). It ends before Tally's own last voucher date - refused."""
    fake, sid = tally
    real = fake.fetch

    def cut_list(conn, table, company, lo, hi):
        out = real(conn, table, company, lo, hi)
        if table["collection"] == "Voucher":
            out = "\r\n".join(l for l in out.split("\r\n") if "2026-04-0" not in l or l < "\t2026-04-06" or "2026-04-0" not in l
                              or int(l.split("2026-04-0")[1][0]) <= 5)
        return out

    monkeypatch.setattr(tc, "fetch", cut_list)
    with pytest.raises(sync.SyncIntegrityError, match="restart Tally"):
        run(db, sid, full=True)
    assert db.scalar("SELECT count(*) FROM tally.trn_voucher") == 0


def test_a_full_sync_that_would_shrink_the_data_sharply_is_stopped(db, tally):
    fake, sid = tally
    run(db, sid, full=True)                              # 9 vouchers stored
    for g in ("v1", "v2", "v3", "v4", "v5"):
        del fake.vouchers[g]                             # Tally now says 4
    with pytest.raises(sync.SyncIntegrityError, match="4 vouchers for this company; 9 are already stored"):
        run(db, sid, full=True)
    assert db.scalar("SELECT count(*) FROM tally.trn_voucher WHERE source_id = ?", [sid]) == 9   # untouched
