-- Tally's own tables, ONE shared copy for every company file, each row tagged
-- with source_id. Generated from
-- tally/database-structure-incremental.sql (the loader's DDL, trimmed to 9 tables):
-- source_id added first, primary keys dropped (a re-sync deletes and
-- re-inserts by (source_id, guid) in one transaction; DuckDB unique
-- constraints have tripped on exactly that pattern). Do not edit by hand:
-- add a new numbered file if the definition changes.
CREATE SCHEMA tally;

CREATE TABLE tally.mst_group (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    name varchar(1024),
    parent varchar(1024),
    _parent varchar(64),
    primary_group varchar(1024),
    is_revenue tinyint,
    is_deemedpositive tinyint,
    is_reserved tinyint,
    affects_gross_profit tinyint,
    sort_position int
);
CREATE TABLE tally.mst_ledger (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    name varchar(1024),
    parent varchar(1024),
    _parent varchar(64),
    alias varchar(256),
    description varchar(64),
    notes varchar(64),
    is_revenue tinyint,
    is_deemedpositive tinyint,
    opening_balance decimal(17,2),
    closing_balance decimal(17,2),
    mailing_name varchar(256),
    mailing_address varchar(1024),
    mailing_state varchar(256),
    mailing_country varchar(256),
    mailing_pincode varchar(64),
    email varchar(256),
    mobile varchar(32),
    it_pan varchar(64),
    gstn varchar(64),
    gst_registration_type varchar(64),
    gst_supply_type varchar(64),
    gst_duty_head varchar(16),
    bank_account_holder varchar(256),
    bank_account_number varchar(64),
    bank_ifsc varchar(64),
    bank_swift varchar(64),
    bank_name varchar(64),
    bank_branch varchar(64),
    bill_credit_period int
);
CREATE TABLE tally.mst_vouchertype (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    name varchar(1024),
    parent varchar(1024),
    _parent varchar(64),
    numbering_method varchar(64),
    is_deemedpositive tinyint,
    affects_stock tinyint
);
CREATE TABLE tally.mst_stock_item (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    name varchar(1024),
    parent varchar(1024),
    _parent varchar(64),
    category varchar(1024),
    _category varchar(64),
    alias varchar(256),
    description varchar(64),
    notes varchar(64),
    part_number varchar(256),
    uom varchar(32),
    _uom varchar(64),
    alternate_uom varchar(32),
    _alternate_uom varchar(64),
    conversion decimal(15,4),
    opening_balance decimal(15,4),
    opening_rate decimal(15,4),
    opening_value decimal(17,2),
    closing_balance decimal(15,4),
    closing_rate decimal(15,4),
    closing_value decimal(17,2),
    costing_method varchar(32),
    gst_type_of_supply varchar(32),
    gst_hsn_code varchar(64),
    gst_hsn_description varchar(256),
    gst_rate decimal(9,4),
    gst_taxability varchar(32)
);
CREATE TABLE tally.mst_cost_centre (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    name varchar(1024),
    parent varchar(1024),
    _parent varchar(64),
    category varchar(1024)
);
CREATE TABLE tally.trn_voucher (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    date date,
    voucher_type varchar(1024),
    _voucher_type varchar(64),
    voucher_number varchar(64),
    reference_number varchar(64),
    reference_date date,
    narration varchar(4000),
    party_name varchar(256),
    _party_name varchar(64),
    place_of_supply varchar(256),
    is_invoice tinyint,
    is_accounting_voucher tinyint,
    is_inventory_voucher tinyint,
    is_order_voucher tinyint
);
CREATE TABLE tally.trn_accounting (
    source_id INTEGER NOT NULL,
    guid varchar(64),
    ledger varchar(1024),
    _ledger varchar(64),
    amount decimal(17,2),
    amount_forex decimal(17,2),
    currency varchar(16)
);
CREATE TABLE tally.trn_inventory (
    source_id INTEGER NOT NULL,
    guid varchar(64),
    item varchar(1024),
    _item varchar(64),
    quantity decimal(15,4),
    rate decimal(15,4),
    amount decimal(17,2),
    additional_amount decimal(17,2),
    discount_amount decimal(17,2),
    godown varchar(1024),
    _godown varchar(64),
    tracking_number varchar(256),
    order_number varchar(256),
    order_duedate date
);
CREATE TABLE tally.trn_cost_centre (
    source_id INTEGER NOT NULL,
    guid varchar(64),
    ledger varchar(1024),
    _ledger varchar(64),
    costcentre varchar(1024),
    _costcentre varchar(64),
    amount decimal(17,2)
);
