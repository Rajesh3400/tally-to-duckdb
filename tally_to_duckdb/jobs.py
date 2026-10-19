"""Running one sync with a recorded sync_runs row.

The row is written RUNNING first and finished whatever happens. A process that
died mid-sync leaves a RUNNING row; `mark_abandoned()` closes those as FAILED
(call it at start-up - no thread can still be running them).

`on_success(db, company_id)` is an optional hook called after a SUCCESS (for
alerts, report rebuilds, ...). Its failure never fails the sync.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from tally_to_duckdb import client as tc
from tally_to_duckdb import db as dbmod
from tally_to_duckdb.sync import Progress, SyncCancelled, SyncResult, rows_json, sync_source

log = logging.getLogger("tally_to_duckdb.sync")


def mark_abandoned(db: dbmod.Db) -> None:
    db.run("UPDATE sync_runs SET status = 'FAILED', finished_at = current_timestamp, "
           "message = 'Process stopped while this sync was running - nothing was changed.' WHERE status = 'RUNNING'")


def run(db: dbmod.Db, source_id: int, full: bool, progress: Progress, cancel: threading.Event,
        on_success: Callable[[dbmod.Db, int], None] | None = None) -> tuple[str, str, SyncResult | None]:
    """Returns (status, message, result)."""
    run_id = db.scalar("INSERT INTO sync_runs (source_id, kind, status) VALUES (?, ?, 'RUNNING') RETURNING id",
                       [source_id, "FULL" if full else "INCREMENTAL"])
    status, message, rows, result = "FAILED", "", "{}", None
    try:
        result = sync_source(db, source_id, full, progress, cancel)
        status, rows = result.status, rows_json(result.rows_loaded)
        if result.status == "SUCCESS" and on_success:
            try:
                on_success(db, db.scalar("SELECT company_id FROM sources WHERE id = ?", [source_id]))
            except Exception:  # noqa: BLE001
                log.exception("on_success hook failed")
        message = ("No change in Tally since the last sync." if result.status == "NO_CHANGE"
                   else ("Tie-out OK. " if result.tie_out_ok else "Tie-out NOT OK. ") + result.tie_out_detail)
    except SyncCancelled:
        status, message = "CANCELLED", "Cancelled - nothing was changed."
    except tc.TallyError as exc:
        message = str(exc)
    except Exception as exc:  # noqa: BLE001 - a sync must always finish its row
        log.exception("sync run %s failed", run_id)
        message = f"{type(exc).__name__}: {exc}"
    finally:
        db.run("UPDATE sync_runs SET status = ?, finished_at = current_timestamp, message = ?, rows_loaded = ? WHERE id = ?",
               [status, message, rows, run_id])
        db.run("UPDATE sources SET last_sync_at = current_timestamp, last_sync_status = ? WHERE id = ?", [status, source_id])
    log.info("sync run %s (source %s): %s", run_id, source_id, status)
    return status, message, result
