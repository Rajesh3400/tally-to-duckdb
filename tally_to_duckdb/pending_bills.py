"""Tally's "Purchase Bills to Come" at a date: goods received, bill not yet
entered - quantity x the receipt note's own rate. Needed only by the tie-out
(Tally's "Profit & Loss A/c" closing may deduct it). The rules are the
pending-bills part of a stock valuation, kept unchanged; stock VALUATION itself
(FIFO etc.) is not part of this project.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from tally_to_duckdb.db import Db

# Tally's standard names for voucher types that record goods moved with the
# invoice not yet raised, used when mst_vouchertype has no row to classify from.
DEFAULT_PENDING_NOTE_TYPES = frozenset({"Goods Receipt Note", "Receipt Note", "Delivery Note"})
PENDING_NOTE_BASES = ("receipt note", "delivery note")


def _normalized_tracking(tracking_number: str | None) -> str:
    """Tracking numbers are typed by hand and vary in case and punctuation, so
    they are compared on alphanumerics alone."""
    return "".join(ch for ch in (tracking_number or "").lower() if ch.isalnum())


def _pending_types(db: Db, source_id: int) -> set[str]:
    pending = set(DEFAULT_PENDING_NOTE_TYPES)
    for name, parent in db.rows("SELECT name, parent FROM tally.mst_vouchertype WHERE source_id = ?", [source_id]):
        if name and (parent or name).strip().lower() in PENDING_NOTE_BASES:
            pending.add(name)
    return pending


def pending_purchase_bills(db: Db, source_id: int, as_of: dt.date) -> Decimal:
    """Positive amount pending at the end of as_of."""
    pending = _pending_types(db, source_id)
    rows = db.rows("""
        SELECT i._item, v.date, i.quantity, i.rate, v.voucher_type, i.tracking_number
        FROM tally.trn_inventory i
        JOIN tally.trn_voucher v ON v.source_id = i.source_id AND v.guid = i.guid
        WHERE i.source_id = ? AND v.is_order_voucher IS DISTINCT FROM 1 AND v.date IS NOT NULL
        ORDER BY i.rowid
    """, [source_id])
    billed_on: dict[tuple[str, str], dt.date] = {}
    for item, date, _q, _r, vtype, tracking in rows:
        if item is None or vtype in pending:
            continue
        key = (_normalized_tracking(tracking), item)
        if key[0] and (key not in billed_on or date < billed_on[key]):
            billed_on[key] = date
    total = Decimal(0)
    for item, date, qty, rate, vtype, tracking in rows:
        if item is None or vtype not in pending:
            continue
        if date > as_of or qty is None or rate is None or Decimal(qty) <= 0:
            continue
        billed = billed_on.get((_normalized_tracking(tracking), item))
        if billed is not None and billed <= as_of:
            continue
        total += Decimal(qty) * Decimal(rate)
    return total
