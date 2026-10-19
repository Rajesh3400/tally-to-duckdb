"""Raw Tally rows -> the tables reports read: dimension keys, fact_line,
bal_month, bal_open. Rebuilt for a WHOLE COMPANY (all its sources) inside the
sync's transaction.

Whole company rather than one source because a source's owned date range
depends on its neighbours: adding a new year's file shrinks what the previous multi-year file owns
(an old file can keep receiving entries after the split). A
company rebuild is a few seconds of set-based SQL, so there is no partial-
rebuild bookkeeping to get wrong.
"""
from __future__ import annotations

from tally_to_duckdb.db import Db

UNALLOCATED = 0  # cc_key for the part of an amount no cost centre covers


def owned_ranges(db: Db, company_id: int) -> list[tuple[int, object, object]]:
    """(source_id, from, to_exclusive|None) - each source owns its books_from
    until the next source starts (Tally's own rule).
    A source never synced (no books_from) owns nothing yet."""
    srcs = db.rows("SELECT id, books_from FROM sources WHERE company_id = ? AND books_from IS NOT NULL "
                   "ORDER BY books_from, id", [company_id])
    return [(sid, start, srcs[i + 1][1] if i + 1 < len(srcs) else None) for i, (sid, start) in enumerate(srcs)]


def rebuild_company(db: Db, company_id: int) -> None:
    ranges = owned_ranges(db, company_id)
    ids = [r[0] for r in ranges] or [-1]
    marks = ",".join("?" for _ in ids)
    with db.tx():
        _dimensions(db, company_id, ids, marks)
        db.run("CREATE OR REPLACE TEMP TABLE _owned (source_id INTEGER, d_from DATE, d_to DATE)")
        if ranges:
            db.cur.executemany("INSERT INTO _owned VALUES (?, ?, ?)", [list(r) for r in ranges])
        db.run("DELETE FROM fact_line WHERE company_id = ?", [company_id])
        # Per (voucher, ledger): the allocations as Tally gives them, plus the
        # remainder on UNALLOCATED. Summed FIRST, then differenced - never a
        # join of allocation rows to accounting rows (the N x N fan-out bug).
        db.run(f"""
            INSERT INTO fact_line
            WITH v AS (
                SELECT v.source_id, v.guid, v.date FROM tally.trn_voucher v
                JOIN _owned o ON o.source_id = v.source_id
                WHERE v.is_accounting_voucher = 1 AND v.date >= o.d_from AND (o.d_to IS NULL OR v.date < o.d_to)
            ),
            acc AS (
                SELECT a.source_id, a.guid, a._ledger, sum(a.amount) AS amount
                FROM tally.trn_accounting a JOIN v USING (source_id, guid)
                GROUP BY ALL
            ),
            alloc AS (
                SELECT c.source_id, c.guid, c._ledger, c._costcentre, sum(c.amount) AS amount
                FROM tally.trn_cost_centre c JOIN v USING (source_id, guid)
                GROUP BY ALL
            ),
            alloc_total AS (SELECT source_id, guid, _ledger, sum(amount) AS amount FROM alloc GROUP BY ALL),
            lines AS (
                SELECT alloc.source_id, alloc.guid, alloc._ledger, ca.cc_key, alloc.amount
                FROM alloc JOIN cc_alias ca ON ca.source_id = alloc.source_id AND ca.guid = alloc._costcentre
                UNION ALL
                SELECT acc.source_id, acc.guid, acc._ledger, {UNALLOCATED}, acc.amount - coalesce(t.amount, 0)
                FROM acc LEFT JOIN alloc_total t USING (source_id, guid, _ledger)
                WHERE acc.amount - coalesce(t.amount, 0) <> 0
            )
            SELECT ?, l.source_id, l.guid, v.date, la.ledger_key, l.cc_key, l.amount
            FROM lines l
            JOIN v USING (source_id, guid)
            JOIN ledger_alias la ON la.source_id = l.source_id AND la.guid = l._ledger
            WHERE l.amount <> 0
        """, [company_id])
        db.run("DELETE FROM bal_month WHERE company_id = ?", [company_id])
        db.run("""
            INSERT INTO bal_month
            SELECT company_id, source_id, date_trunc('month', date)::DATE, ledger_key, cc_key,
                   sum(CASE WHEN amount < 0 THEN -amount ELSE 0 END),
                   sum(CASE WHEN amount > 0 THEN amount ELSE 0 END),
                   sum(amount)
            FROM fact_line WHERE company_id = ? GROUP BY ALL
        """, [company_id])
        db.run("DELETE FROM bal_open WHERE company_id = ?", [company_id])
        db.run(f"""
            INSERT INTO bal_open
            SELECT ?, l.source_id, la.ledger_key, sum(coalesce(l.opening_balance, 0))
            FROM tally.mst_ledger l JOIN ledger_alias la ON la.source_id = l.source_id AND la.guid = l.guid
            WHERE l.source_id IN ({marks}) GROUP BY ALL HAVING sum(coalesce(l.opening_balance, 0)) <> 0
        """, [company_id, *ids])


def _dimensions(db: Db, company_id: int, ids: list[int], marks: str) -> None:
    """One key per (company, name); every source's GUIDs point at it. Names
    come from the masters AND from the postings, so a posting whose ledger
    master is missing still gets a key (and the tie-out names it) instead of
    vanishing from every report."""
    db.run(f"""
        CREATE OR REPLACE TEMP TABLE _ledger_names AS
        SELECT DISTINCT source_id, guid, name FROM tally.mst_ledger WHERE source_id IN ({marks}) AND name IS NOT NULL
        UNION
        SELECT DISTINCT a.source_id, a._ledger, a.ledger FROM tally.trn_accounting a
        WHERE a.source_id IN ({marks}) AND a.ledger IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM tally.mst_ledger m WHERE m.source_id = a.source_id AND m.guid = a._ledger)
    """, [*ids, *ids])
    db.run("""INSERT INTO dim_ledger (company_id, name)
              SELECT DISTINCT ?, name FROM _ledger_names n
              WHERE NOT EXISTS (SELECT 1 FROM dim_ledger d WHERE d.company_id = ? AND d.name = n.name)""",
           [company_id, company_id])
    db.run(f"DELETE FROM ledger_alias WHERE source_id IN ({marks})", ids)
    db.run("""INSERT INTO ledger_alias SELECT DISTINCT n.source_id, n.guid, d.ledger_key
              FROM _ledger_names n JOIN dim_ledger d ON d.company_id = ? AND d.name = n.name""", [company_id])
    # Tally's nature (is_revenue: P&L vs Balance Sheet), from the LATEST file
    # that has the ledger - downstream reports take only P&L postings.
    db.run("""
        UPDATE dim_ledger d SET is_revenue = x.is_revenue FROM (
            SELECT la.ledger_key, arg_max(l.is_revenue = 1, s.books_from) AS is_revenue
            FROM ledger_alias la
            JOIN tally.mst_ledger l ON l.source_id = la.source_id AND l.guid = la.guid
            JOIN sources s ON s.id = la.source_id
            WHERE s.company_id = ? GROUP BY 1
        ) x WHERE d.ledger_key = x.ledger_key""", [company_id])

    db.run(f"""
        CREATE OR REPLACE TEMP TABLE _cc_names AS
        SELECT DISTINCT c.source_id, c.guid, c.name, c.category FROM tally.mst_cost_centre c
        WHERE c.source_id IN ({marks}) AND c.name IS NOT NULL
        UNION
        SELECT DISTINCT t.source_id, t._costcentre, t.costcentre, NULL FROM tally.trn_cost_centre t
        WHERE t.source_id IN ({marks}) AND t.costcentre IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM tally.mst_cost_centre m WHERE m.source_id = t.source_id AND m.guid = t._costcentre)
    """, [*ids, *ids])
    db.run("""INSERT INTO dim_cc (company_id, name, category)
              SELECT ?, name, max(category) FROM _cc_names n
              WHERE NOT EXISTS (SELECT 1 FROM dim_cc d WHERE d.company_id = ? AND d.name = n.name)
              GROUP BY name""", [company_id, company_id])
    db.run(f"DELETE FROM cc_alias WHERE source_id IN ({marks})", ids)
    db.run("""INSERT INTO cc_alias SELECT DISTINCT n.source_id, n.guid, d.cc_key
              FROM _cc_names n JOIN dim_cc d ON d.company_id = ? AND d.name = n.name""", [company_id])
