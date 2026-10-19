"""derive -> tieout -> trial balance on a tiny two-file company built by hand.

File A (books from 1-Apr-25) and file B (from 1-Apr-26) of one company.
Every tricky case the real books have is in here:
- one voucher with the SAME ledger on two lines and a PARTIAL cost-centre
  allocation (an N x N fan-out join would multiply it);
- a Goods Receipt Note (not an accounting voucher) that must not count;
- file A receiving a voucher AFTER file B starts - it belongs to A's raw data
  and Tally's closing balance, but not to the reports (B owns that date);
- Tally's Profit & Loss A/c closing rule (current profit + stock change);
"""
import datetime as dt
from decimal import Decimal

import pytest

from tally_to_duckdb import derive, tieout

D = dt.date


def ins(db, table, **cols):
    names = ", ".join(cols)
    db.run(f"INSERT INTO tally.{table} ({names}) VALUES ({', '.join('?' for _ in cols)})", list(cols.values()))


def ledger(db, sid, guid, name, opening=0, revenue=0, parent="g-root"):
    ins(db, "mst_ledger", source_id=sid, guid=guid, name=name, opening_balance=opening, is_revenue=revenue, _parent=parent)


def voucher(db, sid, guid, date, lines, accounting=1, cc=()):
    ins(db, "trn_voucher", source_id=sid, guid=guid, date=date, is_accounting_voucher=accounting)
    for led_guid, led_name, amount in lines:
        ins(db, "trn_accounting", source_id=sid, guid=guid, _ledger=led_guid, ledger=led_name, amount=amount)
    for led_guid, led_name, cc_guid, cc_name, amount in cc:
        ins(db, "trn_cost_centre", source_id=sid, guid=guid, _ledger=led_guid, ledger=led_name,
            _costcentre=cc_guid, costcentre=cc_name, amount=amount)


def closing(db, sid, kind, guid, value):
    db.run("INSERT INTO tally_closing VALUES (?, ?, ?, ?)", [sid, kind, guid, value])


@pytest.fixture
def books(db):
    cid = 1
    a = db.scalar("INSERT INTO sources (company_id, tally_company_name, books_from) VALUES (?, 'T 25', '2025-04-01') RETURNING id", [cid])
    b = db.scalar("INSERT INTO sources (company_id, tally_company_name, books_from) VALUES (?, 'T 26', '2026-04-01') RETURNING id", [cid])
    for sid in (a, b):
        ins(db, "mst_group", source_id=sid, guid="g-root", name="Primary Group")
        ins(db, "mst_group", source_id=sid, guid="g-stock", name="Stock-in-Hand", _parent="g-root")
        ins(db, "mst_cost_centre", source_id=sid, guid=f"{sid}-cc1", name="Chennai")
        ins(db, "mst_cost_centre", source_id=sid, guid=f"{sid}-cc2", name="Madurai")

    # ---- file A ----
    ledger(db, a, "a-cash", "Cash", opening=-1000)
    ledger(db, a, "a-cap", "Capital", opening=1100)
    ledger(db, a, "a-sales", "Sales", revenue=1)
    ledger(db, a, "a-rent", "Rent", revenue=1)
    ledger(db, a, "a-pl", "Profit & Loss A/c")
    ins(db, "mst_stock_item", source_id=a, guid="a-item", name="Book", opening_value=-100)
    voucher(db, a, "v1", D(2025, 5, 10),
            [("a-cash", "Cash", -500), ("a-sales", "Sales", 300), ("a-sales", "Sales", 200)],
            cc=[("a-sales", "Sales", f"{a}-cc1", "Chennai", 300), ("a-sales", "Sales", f"{a}-cc2", "Madurai", 100)])
    voucher(db, a, "v2", D(2025, 6, 1), [("a-rent", "Rent", -50), ("a-cash", "Cash", 50)], accounting=0)  # GRN
    voucher(db, a, "v3", D(2026, 4, 5), [("a-rent", "Rent", -70), ("a-cash", "Cash", 70)])            # after the split
    for guid, value in (("a-cash", -1430), ("a-cap", 1100), ("a-sales", 500), ("a-rent", -70), ("a-pl", 450)):
        closing(db, a, "LEDGER", guid, value)   # P&L A/c: 0 + (500 - 70) + (-100 - -120) = 450
    closing(db, a, "STOCK", "a-item", -120)

    # ---- file B (new GUIDs, same names) ----
    ledger(db, b, "b-cash", "Cash", opening=-1430)
    ledger(db, b, "b-cap", "Capital", opening=1100)
    ledger(db, b, "b-rent", "Rent", revenue=1)
    ledger(db, b, "b-pl", "Profit & Loss A/c", opening=450)
    ins(db, "mst_stock_item", source_id=b, guid="b-item", name="Book", opening_value=-120)
    voucher(db, b, "v4", D(2026, 4, 20), [("b-rent", "Rent", -30), ("b-cash", "Cash", 30)],
            cc=[("b-rent", "Rent", f"{b}-cc1", "Chennai", -30)])
    for guid, value in (("b-cash", -1400), ("b-cap", 1100), ("b-rent", -30), ("b-pl", 420)):
        closing(db, b, "LEDGER", guid, value)
    closing(db, b, "STOCK", "b-item", -120)

    derive.rebuild_company(db, cid)
    return cid, a, b


def key(db, cid, name):
    return db.scalar("SELECT ledger_key FROM dim_ledger WHERE company_id = ? AND name = ?", [cid, name])


def test_same_name_is_one_key_across_files(db, books):
    cid, a, b = books
    assert db.scalar("SELECT count(DISTINCT ledger_key) FROM ledger_alias WHERE guid IN ('a-cash', 'b-cash')") == 1


def test_fact_line_splits_by_centre_without_fan_out(db, books):
    cid, a, b = books
    sales = dict(db.rows("""SELECT coalesce(c.name, 'UNALLOCATED'), f.amount FROM fact_line f
                            LEFT JOIN dim_cc c ON c.cc_key = f.cc_key WHERE f.ledger_key = ?""", [key(db, cid, "Sales")]))
    assert sales == {"Chennai": Decimal(300), "Madurai": Decimal(100), "UNALLOCATED": Decimal(100)}


def test_non_accounting_and_not_owned_vouchers_stay_out(db, books):
    cid, a, b = books
    assert db.rows("SELECT DISTINCT voucher_guid FROM fact_line WHERE source_id = ? ORDER BY 1", [a]) == [("v1",)]
    assert db.scalar("SELECT sum(amount) FROM fact_line") == 0


def test_tie_out_passes_on_both_files(db, books):
    cid, a, b = books
    for sid in (a, b):
        v = tieout.check_source(db, sid)
        assert v.ok, v.detail


def test_tie_out_names_a_ledger_that_differs(db, books):
    cid, a, b = books
    db.run("UPDATE tally_closing SET closing = -1431 WHERE guid = 'a-cash'")
    v = tieout.check_source(db, a)
    assert not v.ok
    assert [m["ledger"] for m in v.mismatches if m["check"] == "A"] == ["Cash"]


def test_cube_equals_postings(db, books):
    assert db.scalar("SELECT sum(net) FROM bal_month") == db.scalar("SELECT sum(amount) FROM fact_line")
