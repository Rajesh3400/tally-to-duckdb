"""Tally restored from an older backup: its AlterIDs fall BELOW our watermark.
An incremental sync then deletes what Tally no longer has and fetches nothing
(every row is below the old watermark) - a real company lost its
changed vouchers that way. The sync must notice and run FULL instead."""
import datetime as dt
import threading

from tally_to_duckdb import client as tc
from tally_to_duckdb import sync


def test_older_tally_copy_forces_a_full_sync(db, monkeypatch):
    cid = 1
    sid = db.scalar("""INSERT INTO sources (company_id, tally_company_name, books_from, alterid_master, alterid_transaction)
                       VALUES (?, 'T 26', '2026-04-01', 5000, 3000) RETURNING id""", [cid])
    db.run("INSERT INTO tally.trn_voucher (source_id, guid, date, is_accounting_voucher) VALUES (?, 'v-old', '2026-04-02', 1)", [sid])

    monkeypatch.setattr(tc, "set_company", lambda *_: None)
    monkeypatch.setattr(tc, "company_info", lambda *_: tc.CompanyInfo("T 26", dt.date(2026, 4, 1), dt.date(2026, 9, 5), 4500, 2900))
    monkeypatch.setattr(tc, "date_windows",
                        lambda conn, company, lo, hi, extra=None: tc.Windows([(lo, hi)], 1, [1], dt.date(2026, 9, 5)))
    filters_seen = []

    def fake_fetch(conn, table, company, lo, hi):
        filters_seen.extend(table.get("filters") or [])
        names = [f["name"] for f in table["fields"]]
        header = "\t".join(names)
        if table["collection"] == "Voucher" and "is_accounting_voucher" in names:   # Tally's (older) copy: one voucher
            row = {"guid": "v-new", "date": "2026-04-02", "is_accounting_voucher": "0"}
            return header + "\r\n" + "\t".join(row.get(n, "") for n in names)
        return header

    monkeypatch.setattr(tc, "fetch", fake_fetch)
    monkeypatch.setattr(tc, "closings", lambda *_: "kind\tguid\tclosing")
    messages = []
    result = sync.sync_source(db, sid, False, lambda m, d, t: messages.append(m), threading.Event())

    assert result.status == "SUCCESS"
    assert any("older copy" in m for m in messages)
    assert not any("$AlterID >" in f for f in filters_seen)       # full fetch, not incremental
    assert db.rows("SELECT guid FROM tally.trn_voucher WHERE source_id = ?", [sid]) == [("v-new",)]  # replaced by Tally's copy
    assert db.rows("SELECT alterid_master, alterid_transaction FROM sources WHERE id = ?", [sid]) == [(4500, 2900)]
