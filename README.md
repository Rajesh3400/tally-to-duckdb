# tally-to-duckdb

Pulls the books of one or more **Tally Prime** companies out of Tally's XML/HTTP
port and writes them into a single **DuckDB** file: Tally's own tables (masters
and vouchers), plus a small derived model (ledger/cost-centre dimensions, a
posting table at cost-centre grain, monthly balances, opening balances). Every
sync ends with a **tie-out** that compares the numbers in the database with the
closing balances Tally itself reports.

It is for people who want their Tally data in SQL (reporting, BI, audit
checks) and need to trust it: it is built around the ways a real Tally returns
wrong, partial or duplicated answers, and it refuses to write data it cannot
verify. It is read-only towards Tally - it never writes back.

```text
 Tally Prime  (XML over HTTP, e.g. localhost:9000)
      |
      v
 fetch   - date-windowed requests with an explicit TDL date filter
           (each answer: re-asked if cut off, judged against Tally's own counts)
      |
      v
 stage   - every table into a scratch schema (nothing live is touched yet)
      |
      v
 ONE transaction:  replace/merge raw tally.* rows + closings + AlterID watermarks
                   + re-derive dimensions / fact_line / bal_month / bal_open
      |
      v
 tie-out - ours vs Tally's closing balances, after the commit
```

## Why this exists

Tally is the accounting system of record for a very large share of small and
mid-sized businesses, especially in India. It is excellent at bookkeeping but
rigid as a reporting source: custom reports mean TDL programming, Excel exports
that go stale the moment they are taken, or manual re-keying - none of which
scales to multi-company groups, month-on-month analysis, or reconciliation
against other systems such as bank statements.

tally-to-duckdb keeps a **complete, verified mirror** of Tally in one DuckDB
file that any SQL tool, BI dashboard, notebook or spreadsheet can query. Reports
are built on the mirror instead of on Tally's export screens; Tally stays the
system of record.

What makes the mirror trustworthy rather than "just an export":

- **Proven complete.** Every voucher is fetched exactly once, every accounting
  voucher balances, and every ledger's closing equals Tally's to the paisa. A
  pull that fails these checks is refused - never half-written.
- **Defends against Tally's own quirks** found in production: ignored date
  filters, short or empty answers under memory pressure, AlterIDs that go
  backwards after a restore, ledger closings that go stale under incremental
  sync (see [Limitations](#limitations-and-tally-quirks)).
- **Efficient.** One fact table holds every posting of every company and
  financial year at cost-centre grain, backed by a 9-table raw mirror; only the
  Tally collections actually used are fetched.
- **Incremental.** Nightly syncs fetch only what changed, with a forced full
  resync whenever Tally's history is rewritten.

It was extracted from the data layer of a multi-company MIS running in
production, where its figures were checked against the previous system with
zero differences across several thousand reported numbers.

## Requirements and install

- Tally Prime running on the PC, the company open, and its **XML/HTTP server
  enabled**: in Tally, `Help > Settings > Connectivity > Client/Server
  configuration` (menu wording varies by release), set *TallyPrime acts as* to
  **Server** or **Both**, and note the port (default 9000).
- Python 3.11+. Developed and tested on Windows only.

```text
git clone <this repository>
cd tally-to-duckdb
python -m venv .venv
.venv\Scripts\activate
pip install -e .          # add [dev] for the tests
```

## Usage

### 1. Create the database

```text
> tally-to-duckdb init --db books.duckdb
Database ready: books.duckdb
```

You can also set `T2D_DB=books.duckdb` once instead of repeating `--db`.

### 2. Register the Tally company files

```text
> tally-to-duckdb add-source --company "Acme Traders 25-26" --books-from 2025-04-01
Added source 1: "Acme Traders 25-26" (entity 1, books from 2025-04-01).
```

- `--company` must equal the company name in Tally **character for character**.
- One *source* = one Tally company file. A file may hold one year or several.
  If a business has a file per year, add one source per file and pass the same
  `--entity-id` so they are treated as one entity.
- Each date is owned by exactly one file: a source owns the dates from its
  `books_from` until the next source of the same entity begins. A voucher keyed
  into an old file after the split is kept in the raw tables but not counted in
  the derived tables, so nothing is counted twice.
- `books_from` is replaced by Tally's own value on the first sync.

### 3. Sync

```text
> tally-to-duckdb sync                     # every source, incremental
> tally-to-duckdb sync --company "Acme Traders 25-26" --full
== Acme Traders 25-26
   [0/34] Connecting to Tally...
   [3/34] Fetching trn_voucher from Tally (batch 1/12)...
   ...
   [34/34] Writing to the database...
   SUCCESS: Tie-out OK. A. All 3,550 ledgers equal Tally's closing balance ...
```

- **Incremental** (default) asks Tally only for what changed since the stored
  AlterID watermarks, and finds deletions by comparing full GUID lists. A source
  that has never synced is always synced in full. `--full` replaces the
  source's data.
- A source whose Tally AlterIDs are unchanged reports `NO_CHANGE`.
- Progress lines print as the sync moves. Tally answers that were rejected and
  re-asked are logged as warnings; `-v` adds info-level lines. Each run is
  recorded in `sync_runs`.
- **Cancel** with Ctrl+C: the sync stops after the current Tally request and
  nothing is changed (the write is one transaction).
- Only one sync can run at a time (DuckDB allows one writer process).
- Exit code is non-zero if any source failed.

### 4. Tie-out

```text
> tally-to-duckdb tieout
== Acme Traders 25-26: OK
A. All 3,550 ledgers equal Tally's closing balance
B. Report postings equal Tally's raw lines for every ledger
C. Monthly totals equal postings
```

- **A - raw vs Tally.** For every ledger, opening + all posted movement must
  equal the closing balance Tally reports (re-fetched in full on each sync).
  This proves the *sync*: nothing lost, nothing doubled. *Profit & Loss A/c*
  is the one ledger with a rule: Tally's closing also carries the period's P&L
  movement and the stock-value change (and may deduct pending purchase bills).
- **B - fact vs raw.** `fact_line` sums to the raw accounting lines over the
  dates the source owns. Proves the cost-centre split and ownership rule.
- **C - cube vs fact.** `bal_month` sums to `fact_line`.
- A failure prints the count and the largest differences ("ledger: ours X vs
  Tally Y"). A failure in A (a few ledgers) usually means Tally's data changed
  during the sync or Tally answered wrongly: sync again, `--full` if it
  persists. A failure in B or C points at a defect in the derivation - please
  report it.

### 5. Status and sources

```text
> tally-to-duckdb status
> tally-to-duckdb list-sources
```

### Scheduling nightly (Windows Task Scheduler)

```text
schtasks /Create /SC DAILY /ST 02:00 /TN "tally-to-duckdb" ^
  /TR "cmd /c cd /d C:\tally-sync && .venv\Scripts\tally-to-duckdb.exe sync --db books.duckdb >> sync.log 2>&1"
```

Tally and the company must be open at that time. Don't open the database file
from another program (BI tool, shell) while a sync runs. This tool has no
scheduler of its own and syncs every registered source it is asked to: remove
or skip sources whose year file is archived (not open in Tally) by naming the
ones you want with `--company`.

### Configuration

Every option can be an environment variable, a command-line flag (before or
after the command), or a key in a TOML file named by `T2D_CONFIG`
(keys = the setting names in `config.py`). Precedence: flag > env > TOML > default.

| Setting | Env var | Flag | Default |
|---|---|---|---|
| Database file | `T2D_DB` | `--db` | `tally.duckdb` |
| Tally host | `T2D_TALLY_HOST` | `--host` | `localhost` |
| Tally XML port | `T2D_TALLY_PORT` | `--port` | `9000` |
| Protocol (`http`/`https`) | `T2D_TALLY_PROTOCOL` | - | `http` |
| Basic-auth user (behind a proxy) | `T2D_TALLY_USER` | - | empty |
| Basic-auth password | `T2D_TALLY_PASSWORD` | - | empty |
| DuckDB memory limit | `T2D_MEMORY_LIMIT` | `--memory-limit` | `600MB` |
| DuckDB threads | `T2D_THREADS` | `--threads` | `2` |
| Config file | `T2D_CONFIG` | - | none |
| Verbose log | - | `-v` | off |

## Database structure

Everything is in one DuckDB file. No foreign keys are declared (a re-sync
deletes and re-inserts rows in one transaction, and DuckDB's constraint checks
have tripped on that pattern); joins are by the columns below.

### Raw layer: schema `tally`

Nine tables, each with a leading `source_id` column and **no primary keys**
(`schema/002_tally_raw.sql`). Within a source a row is identified by `guid`
(voucher child tables carry the voucher's `guid` plus `_ledger`, `_costcentre`
GUID references). Row order is preserved (insertion order), which stock
calculations built on top rely on.

| Table | Holds | Needed because |
|---|---|---|
| `mst_group` | Tally's group tree (`parent`, `primary_group`) | rename cascade into `mst_ledger.parent` (`sync.py` cascade_update); the group tree for reports built on the mirror |
| `mst_ledger` | ledgers and their opening balance, `is_revenue` | `derive.py` dim_ledger, `tieout.py` checks, `_verify` master-reference check |
| `mst_vouchertype` | voucher types | `pending_bills.py` (base type), auto-numbering refresh in `sync.py` |
| `mst_cost_centre` | cost centres and categories | `derive.py` dim_cc, `_verify` master-reference check |
| `mst_stock_item` | stock items, opening value | tie-out Profit & Loss A/c rule (`tieout.py`) |
| `trn_voucher` | one row per voucher: date, type, number | the incremental diff, the integrity counts (`_verify`, `_judge_window`), `fact_line` |
| `trn_accounting` | ledger lines of a voucher | `fact_line`, tie-out, window checks (lines must net to zero) |
| `trn_cost_centre` | cost-centre allocations of a ledger line | `fact_line` at cost-centre grain |
| `trn_inventory` | stock lines of a voucher | `pending_bills.py` (purchase bills to come) |

The upstream Tally Database Loader defines 32 collections. The other 23 (UoM,
godown, stock categories/groups, cost categories, employees/payheads/attendance,
GST rates, batch and bill allocations, bank allocations, standard cost/price and
the extra cost-allocation tables) are **deliberately not fetched**, so each sync
makes 23 fewer requests to Tally and stores less. To add one back: copy its
block from the upstream `tally-export-config-incremental.yaml` and
`database-structure-incremental.sql` into `tally/`, add its `CREATE TABLE
tally.<name>` (with `source_id INTEGER NOT NULL` first) in a new numbered file
under `schema/`, and re-add any `cascade_update` / `cascade_delete` entries that
point at it. Run a full sync once to fill it.

### Control tables

| Table | Holds |
|---|---|
| `sources` | one row per Tally company file: `company_id` (entity), `tally_company_name`, `books_from`, `last_voucher_date`, the two AlterID **watermarks** (`alterid_master`, `alterid_transaction`), last sync status, tie-out verdict |
| `sync_runs` | one row per run: kind (FULL / INCREMENTAL), status, times, message, rows loaded per table (JSON) |
| `tally_closing` | Tally's own closing balance per ledger and closing value per stock item, as at the source's last voucher date; the independent side of the tie-out |
| `schema_version` | applied schema files |

### Derived model

| Table | Grain / key columns | Joins |
|---|---|---|
| `dim_ledger` | one row per (entity, ledger **name**): `ledger_key`, `company_id`, `name`, `is_revenue` (P&L vs balance sheet, from the latest file holding it) | `ledger_alias.ledger_key` |
| `ledger_alias` | (`source_id`, `guid`) -> `ledger_key` | `tally.mst_ledger.guid` |
| `dim_cc` | one row per (entity, cost-centre **name**): `cc_key`, `name`, `category` | `cc_alias.cc_key` |
| `cc_alias` | (`source_id`, `guid`) -> `cc_key` | `tally.mst_cost_centre.guid` |
| `fact_line` | one row per (voucher, ledger, cost centre): `company_id`, `source_id`, `voucher_guid`, `date`, `ledger_key`, `cc_key`, `amount` (Tally sign: debit negative) | dims by key |
| `bal_month` | (`company_id`, `source_id`, `month`, `ledger_key`, `cc_key`): `debit`, `credit`, `net` | dims by key |
| `bal_open` | (`company_id`, `source_id`, `ledger_key`): opening balance at the source's `books_from` | dims by key |

`fact_line` carries the cost-centre allocations as Tally gives them, plus the
remainder of each (voucher, ledger) amount on `cc_key = 0` ("unallocated"), so
totals foot even when allocations are partial. Only accounting vouchers
(`is_accounting_voucher = 1`) are included - a goods receipt or order can
carry ledger lines that Tally does not post.

Example - trial balance of one source (file) at a date (Tally sign: debit
negative):

```sql
SELECT d.name, coalesce(o.amt, 0) + coalesce(m.amt, 0) AS balance
FROM dim_ledger d
LEFT JOIN (SELECT ledger_key, sum(amount) AS amt FROM bal_open
           WHERE source_id = 1 GROUP BY 1) o USING (ledger_key)
LEFT JOIN (SELECT ledger_key, sum(amount) AS amt FROM fact_line
           WHERE source_id = 1 AND date <= DATE '2026-03-31' GROUP BY 1) m USING (ledger_key)
WHERE coalesce(o.amt, 0) + coalesce(m.amt, 0) <> 0;
```

(Opening balances are per file, at that file's `books_from`; across several
year files, pick the file whose range covers your date.) Monthly movement of one
ledger:

```sql
SELECT month, sum(debit) AS debit, sum(credit) AS credit, sum(net) AS net
FROM bal_month b JOIN dim_ledger d USING (ledger_key)
WHERE d.company_id = 1 AND d.name = 'Cash' GROUP BY month ORDER BY month;
```

## Design choices

- **DuckDB, one file, columnar.** Set-based SQL does the derivation in
  seconds; the file is easy to copy, back up, and query from anywhere.
- **Dimensions are keyed by NAME across year files.** Tally gives the same
  ledger a different GUID in each year's file; one key per (entity, name) means
  a ledger is classified once and every year inherits it.
- **Whole-entity re-derive, not incremental derive.** What a file owns depends
  on its neighbours, and a rebuild is a few seconds of SQL, so there is no
  partial-rebuild bookkeeping to get wrong. Allocations are summed *before*
  being differenced from the accounting lines - never joined row to row (an
  N x N fan-out would multiply amounts).
- **Closings are re-fetched on every sync.** A ledger's AlterID does not move
  when only its vouchers change, so an incremental sync leaves the stored
  ledger closing stale. The independent side of the tie-out must not be stale.
- **One write transaction.** Everything is fetched into a scratch schema first.
  Raw rows, closings, watermarks and the derived tables change together or not
  at all. The tie-out runs **after** the commit: it only reads, and inside the
  transaction its joins plus the uncommitted old and new rows exhausted memory
  on a large file.
- **Memory.** `T2D_MEMORY_LIMIT` caps DuckDB's working set; beyond it DuckDB
  spills to a `duckdb_tmp` folder next to the database. Staged data is loaded
  from a temporary file rather than a Python file object (DuckDB keeps one
  object store per database and a failed run could poison later ones).

## Speed

Measured on **one installation**; your numbers will vary, mostly with Tally.

- Full sync of a company year with about 50,000 vouchers: roughly **2-15
  minutes**. Tally is the bottleneck - the same pull took minutes on a fresh
  Tally and up to an hour on a loaded one. Incremental syncs are much smaller.
- A rehearsal pulling ~50k vouchers and ~170k accounting lines in 12 windows,
  checking that every voucher balanced, took about **2 minutes**.
- Only the 9 collections the code reads are requested (the upstream loader
  has 32), so a sync makes far fewer requests to Tally than a full mirror would.
- A trial-balance style query over the derived tables ran in roughly
  **0.1-0.3 s** on a ~3,500-ledger company.

## Limitations and Tally quirks

- **Tally ignores `SVFROMDATE`/`SVTODATE` for voucher collections.** A request
  for two days returned the whole year. Windowing therefore uses an explicit TDL
  filter comparing the voucher date as a yyyymmdd *number* (a date-literal
  comparison returned nothing). Without it a batched full sync stores each
  voucher several times. A voucher seen in two batches aborts the sync.
- **A loaded Tally returns incomplete or empty answers**, sometimes well-formed
  and *identical twice*. The tool re-asks cut-off answers, checks each batch
  against Tally's own voucher count, checks that accounting vouchers come back
  with balanced lines, requires the voucher list to end on the company's
  `LastVoucherDate`, and refuses to replace data when a full pull returns fewer
  than 90% of the vouchers already stored. If it refuses: **restart Tally**,
  open the company, sync again. No code can extract complete data from a Tally
  that is starved of memory.
- **AlterIDs can go backwards** (company restored from an older backup). An
  incremental sync would then delete what the old copy lacks and fetch nothing
  below the old watermark, so the tool forces a full sync when Tally's AlterID
  is below the stored one.
- **"Profit & Loss A/c"** is a derived ledger; its closing is checked with the
  rule above, either with or without pending purchase bills.
- **Stock valuation is not included** (no FIFO/average costing, no stock
  reports). Only the raw `trn_inventory` table and what the tie-out needs
  (`pending_bills.py`) are present. No GST or other statutory reports.
- One sync at a time (single DuckDB writer). Only Tally Prime on Windows has
  been tested. Read-only towards Tally.

## Testing

```text
pip install -e .[dev]
pytest
```

The sync tests run against a **fake Tally** (`tests/test_sync_fake_tally.py`)
that applies the real requests' filters and can mimic the quirks above: a Tally
that ignores date windows, empty or short answers, a cut-off voucher, a voucher
count that changes, a full sync that would shrink the data, and incremental
sync after change/delete/add/back-dated add. `test_sync_watermark.py` covers
AlterIDs that fell. Derivation and tie-out are tested on a hand-built two-file
company.

## Layout

```text
tally_to_duckdb/   client.py (Tally HTTP/XML)  sync.py (fetch, judge, write)
                   derive.py  tieout.py  pending_bills.py  jobs.py  db.py
                   config.py  cli.py
schema/            001_sources.sql (control + derived)  002_tally_raw.sql
tally/             the loader's export definition and DDL (unchanged)
tests/
```

`jobs.run(..., on_success=callback)` is an optional hook called after each
successful sync.

## Contributing

Issues and pull requests are welcome. Keep behaviour changes covered by a test
against the fake Tally; schema changes go in a new numbered file (never edit a
shipped one).

## Licence and credit

MIT - see [LICENSE](LICENSE). The Tally export definition and table
definitions come from the open-source
[Tally Database Loader](https://github.com/dhananjay1405/tally-database-loader)
by dhananjay1405 (MIT); this project builds on its design and does not claim
it. See [NOTICE](NOTICE).
