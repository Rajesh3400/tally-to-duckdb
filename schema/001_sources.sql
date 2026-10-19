-- Tally files, sync runs, and the derived tables built from the raw Tally rows.

-- One row per Tally COMPANY FILE. A file may hold one year or several. A source OWNS the dates from its
-- books_from until the next source of the same company starts (Tally's own
-- rule); fact_line holds only owned dates, so an
-- old file that kept receiving entries after the split never double counts.
CREATE SEQUENCE seq_sources START 1;
CREATE TABLE sources (
    id                  INTEGER PRIMARY KEY DEFAULT nextval('seq_sources'),
    company_id          INTEGER NOT NULL,         -- sources of one entity (e.g. its yearly files) share this
    tally_company_name  VARCHAR NOT NULL UNIQUE,  -- must equal Tally's exactly
    books_from          DATE,                     -- from Tally on first sync
    last_voucher_date   DATE,
    alterid_master      BIGINT NOT NULL DEFAULT 0, -- watermarks, written WITH the data
    alterid_transaction BIGINT NOT NULL DEFAULT 0,
    last_sync_at        TIMESTAMPTZ,
    last_sync_status    VARCHAR,
    -- Result of the post-sync tie-out against Tally's own closing balances.
    tie_out_ok          BOOLEAN,
    tie_out_detail      VARCHAR
);

CREATE SEQUENCE seq_sync_runs START 1;
CREATE TABLE sync_runs (
    id          INTEGER PRIMARY KEY DEFAULT nextval('seq_sync_runs'),
    source_id   INTEGER NOT NULL,
    kind        VARCHAR NOT NULL CHECK (kind IN ('FULL', 'INCREMENTAL', 'IMPORT')),
    status      VARCHAR NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED', 'NO_CHANGE')),
    started_at  TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    finished_at TIMESTAMPTZ,
    message     VARCHAR,
    rows_loaded VARCHAR   -- JSON {table: rows}
);

-- Tally's own closing figures, re-fetched IN FULL on every sync. Not taken
-- from mst_ledger.closing_balance: an incremental sync re-sends only masters
-- whose AlterID moved, and a ledger's AlterID does not move when only its
-- vouchers do, so that column goes stale (observed: hundreds of ledgers
-- off). Kind 'LEDGER' = ledger closing balance, 'STOCK' = stock item closing
-- value; both as of the source's last voucher date.
CREATE TABLE tally_closing (
    source_id INTEGER NOT NULL,
    kind      VARCHAR NOT NULL,
    guid      VARCHAR NOT NULL,
    closing   DECIMAL(17, 2) NOT NULL
);

-- Identity across years: one key per (company, name).
-- Every year's GUID for that name points at the same key, so a ledger is
-- classified once and every year inherits it.
CREATE SEQUENCE seq_ledger_key START 1;
CREATE TABLE dim_ledger (
    ledger_key  INTEGER PRIMARY KEY DEFAULT nextval('seq_ledger_key'),
    company_id  INTEGER NOT NULL,
    name        VARCHAR NOT NULL,
    -- Tally's nature: TRUE = profit & loss ledger, FALSE = balance sheet. From the latest file holding the ledger.
    is_revenue  BOOLEAN,
    UNIQUE (company_id, name)
);
CREATE TABLE ledger_alias (
    source_id  INTEGER NOT NULL,
    guid       VARCHAR NOT NULL,
    ledger_key INTEGER NOT NULL
);

-- cc_key 0 = UNALLOCATED: the part of a (voucher, ledger) amount no cost
-- centre allocation covers. Never a row in dim_cc.
CREATE SEQUENCE seq_cc_key START 1;
CREATE TABLE dim_cc (
    cc_key     INTEGER PRIMARY KEY DEFAULT nextval('seq_cc_key'),
    company_id INTEGER NOT NULL,
    name       VARCHAR NOT NULL,
    category   VARCHAR,
    UNIQUE (company_id, name)
);
CREATE TABLE cc_alias (
    source_id INTEGER NOT NULL,
    guid      VARCHAR NOT NULL,
    cc_key    INTEGER NOT NULL
);

-- Every posting of every company, at cost-centre grain:
-- the cost-centre allocations as Tally gives them, plus the remainder of each
-- (voucher, ledger) amount on cc_key 0. Built per (voucher, ledger) AFTER
-- summing, never by joining allocations to accounting lines (the N x N fan-out).
-- Accounting vouchers only, and only dates the source owns.
-- amount: Tally's sign (negative = debit).
CREATE TABLE fact_line (
    company_id   INTEGER NOT NULL,
    source_id    INTEGER NOT NULL,
    voucher_guid VARCHAR NOT NULL,
    date         DATE NOT NULL,
    ledger_key   INTEGER NOT NULL,
    cc_key       INTEGER NOT NULL,
    amount       DECIMAL(17, 2) NOT NULL
);

-- Monthly totals of fact_line, rebuilt with it in the sync transaction, so it
-- can never disagree with the postings (no cache, no fingerprint).
CREATE TABLE bal_month (
    company_id INTEGER NOT NULL,
    source_id  INTEGER NOT NULL,
    month      DATE NOT NULL,      -- first day of the month
    ledger_key INTEGER NOT NULL,
    cc_key     INTEGER NOT NULL,
    debit      DECIMAL(19, 2) NOT NULL,  -- positive
    credit     DECIMAL(19, 2) NOT NULL,  -- positive
    net        DECIMAL(19, 2) NOT NULL   -- Tally sign
);

-- Each ledger's opening balance as of its source's books_from (Tally's master
-- figure), keyed for reports.
CREATE TABLE bal_open (
    company_id INTEGER NOT NULL,
    source_id  INTEGER NOT NULL,
    ledger_key INTEGER NOT NULL,
    amount     DECIMAL(17, 2) NOT NULL
);
