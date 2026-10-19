"""The post-sync verdict: do our numbers equal Tally's own? Three checks.

A. RAW vs TALLY, per ledger: opening + every posted movement = the closing
   balance Tally itself reports (re-fetched in full each sync). Proven exact
   on real company files with ONE rule-shaped exception:
   Tally's "Profit & Loss A/c" closing also carries the current profit and the
   stock change -
       closing = opening + its own movement + all P&L-ledger movement
                 + (opening stock value - closing stock value)
                 - Purchase Bills to Come at the period end
   (raw Tally signs). Whether Tally deducts the pending bills in that closing
   depends on the file, so either exact match is accepted for that ONE ledger.
   This proves the SYNC: nothing lost, nothing doubled.
B. FACT vs RAW, per ledger: fact_line (owned dates only) sums to the raw
   accounting lines over the same dates. Proves the cost-centre split and the
   owned-range rule lose nothing.
C. CUBE vs FACT, per ledger: bal_month sums to fact_line. Proves the cube.

Only accounting vouchers count everywhere (is_accounting_voucher = 1) - a GRN
or order carries ledger lines Tally does not post.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tally_to_duckdb.db import Db
from tally_to_duckdb import pending_bills
from tally_to_duckdb.derive import owned_ranges

PL_ACCOUNT = "Profit & Loss A/c"   # Tally's reserved ledger name


@dataclass
class Verdict:
    ok: bool
    detail: str
    mismatches: list[dict]


def _fmt(x) -> str:
    return f"{Decimal(x or 0):,.2f}"


def check_source(db: Db, source_id: int) -> Verdict:
    problems: list[dict] = []
    lines: list[str] = []

    if not db.scalar("SELECT count(*) FROM tally_closing WHERE source_id = ? AND kind = 'LEDGER'", [source_id]):
        return Verdict(False, "Tally's closing balances were not fetched - re-run the sync.", [])

    # Tally's derived "Purchase Bills to Come" (goods received, bill not yet
    # entered) is an expense in its P&L with no ledger posting: its P&L A/c
    # closing is lower by the amount pending at the period end (pending_bills.py).
    last = db.scalar("SELECT last_voucher_date FROM sources WHERE id = ?", [source_id])
    pending = pending_bills.pending_purchase_bills(db, source_id, last) if last else Decimal(0)
    a = db.all("""
        WITH mv AS (
            SELECT a._ledger AS guid, sum(a.amount) AS m
            FROM tally.trn_accounting a JOIN tally.trn_voucher v ON v.source_id = a.source_id AND v.guid = a.guid
            WHERE a.source_id = $1 AND v.is_accounting_voucher = 1 GROUP BY 1
        ),
        pl AS (
            SELECT coalesce(sum(mv.m), 0) AS m FROM mv
            JOIN tally.mst_ledger l ON l.source_id = $1 AND l.guid = mv.guid WHERE l.is_revenue = 1
        ),
        stock AS (
            SELECT coalesce((SELECT sum(opening_value) FROM tally.mst_stock_item WHERE source_id = $1), 0)
                 - coalesce((SELECT sum(closing) FROM tally_closing WHERE source_id = $1 AND kind = 'STOCK'), 0)
                 AS change
        ),
        x AS (
            SELECT l.name, coalesce(l.opening_balance, 0) + coalesce(mv.m, 0)
                     + CASE WHEN l.name = $2 THEN pl.m + stock.change ELSE 0 END AS ours,
                   coalesce(tc.closing, 0) AS tally
            FROM tally.mst_ledger l
            CROSS JOIN pl CROSS JOIN stock
            LEFT JOIN mv ON mv.guid = l.guid
            LEFT JOIN tally_closing tc ON tc.source_id = $1 AND tc.kind = 'LEDGER' AND tc.guid = l.guid
            WHERE l.source_id = $1
        )
        -- Profit & Loss A/c: Tally's figure may or may not deduct the pending
        -- purchase bills ($3) - some files do, some do not. Either
        -- exact match is accepted for that ONE derived ledger; every other
        -- ledger must match as is.
        SELECT * FROM x WHERE abs(ours - tally) >= 0.01
          AND NOT (name = $2 AND abs(ours - $3 - tally) < 0.01)
        ORDER BY abs(ours - tally) DESC
    """, [source_id, PL_ACCOUNT, pending])
    n_ledgers = db.scalar("SELECT count(*) FROM tally.mst_ledger WHERE source_id = ?", [source_id])
    if a:
        lines.append(f"A. {len(a)} of {n_ledgers:,} ledgers differ from Tally's closing balance")
        problems += [{"check": "A", "ledger": r["name"], "ours": float(r["ours"]), "tally": float(r["tally"] or 0)} for r in a]
    else:
        lines.append(f"A. All {n_ledgers:,} ledgers equal Tally's closing balance")

    company_id = db.scalar("SELECT company_id FROM sources WHERE id = ?", [source_id])
    d_from, d_to = next(((f, t) for s, f, t in owned_ranges(db, company_id) if s == source_id), (None, None))
    b = db.all("""
        WITH raw AS (
            SELECT la.ledger_key, sum(a.amount) AS amount
            FROM tally.trn_accounting a
            JOIN tally.trn_voucher v ON v.source_id = a.source_id AND v.guid = a.guid
            JOIN ledger_alias la ON la.source_id = a.source_id AND la.guid = a._ledger
            WHERE a.source_id = $1 AND v.is_accounting_voucher = 1
              AND v.date >= $2 AND ($3::DATE IS NULL OR v.date < $3)
            GROUP BY 1
        ),
        fact AS (SELECT ledger_key, sum(amount) AS amount FROM fact_line WHERE source_id = $1 GROUP BY 1)
        SELECT d.name, coalesce(raw.amount, 0) AS raw, coalesce(fact.amount, 0) AS fact
        FROM raw FULL JOIN fact USING (ledger_key) JOIN dim_ledger d USING (ledger_key)
        WHERE coalesce(raw.amount, 0) <> coalesce(fact.amount, 0)
    """, [source_id, d_from, d_to])
    if b:
        lines.append(f"B. {len(b)} ledgers: report postings differ from Tally's raw lines")
        problems += [{"check": "B", "ledger": r["name"], "ours": float(r["fact"]), "tally": float(r["raw"])} for r in b]
    else:
        lines.append("B. Report postings equal Tally's raw lines for every ledger")

    c = db.scalar("""
        SELECT count(*) FROM (
            SELECT ledger_key, sum(amount) AS x FROM fact_line WHERE source_id = $1 GROUP BY 1
        ) f FULL JOIN (
            SELECT ledger_key, sum(net) AS y FROM bal_month WHERE source_id = $1 GROUP BY 1
        ) m USING (ledger_key)
        WHERE coalesce(x, 0) <> coalesce(y, 0)
    """, [source_id])
    lines.append(f"C. {c} ledgers: monthly totals differ from postings" if c else "C. Monthly totals equal postings")
    if c:
        problems.append({"check": "C", "ledger": f"{c} ledgers", "ours": 0, "tally": 0})

    if a:
        top = "; ".join(f"{r['name']}: ours {_fmt(r['ours'])} vs Tally {_fmt(r['tally'])}" for r in a[:3])
        lines.append("Largest: " + top)
    return Verdict(not problems, "\n".join(lines), problems)
