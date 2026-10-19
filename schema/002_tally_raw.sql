-- Tally's own tables, ONE shared copy for every company file, each row tagged
-- with source_id. Generated from
-- tally/database-structure-incremental.sql (the loader's DDL):
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
CREATE TABLE tally.mst_uom (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    name varchar(1024),
    formalname varchar(256),
    is_simple_unit tinyint,
    base_units varchar(1024),
    additional_units varchar(1024),
    conversion decimal(15,4)
);
CREATE TABLE tally.mst_godown (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    name varchar(1024),
    parent varchar(1024),
    _parent varchar(64),
    address varchar(1024)
);
CREATE TABLE tally.mst_stock_category (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    name varchar(1024),
    parent varchar(1024),
    _parent varchar(64)
);
CREATE TABLE tally.mst_stock_group (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    name varchar(1024),
    parent varchar(1024),
    _parent varchar(64)
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
CREATE TABLE tally.mst_cost_category (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    name varchar(1024),
    allocate_revenue tinyint,
    allocate_non_revenue tinyint
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
CREATE TABLE tally.mst_attendance_type (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    name varchar(1024),
    parent varchar(1024),
    _parent varchar(64),
    uom varchar(32),
    _uom varchar(64),
    attendance_type varchar(64),
    attendance_period varchar(64)
);
CREATE TABLE tally.mst_employee (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    name varchar(1024),
    parent varchar(1024),
    _parent varchar(64),
    id_number varchar(256),
    date_of_joining date,
    date_of_release date,
    designation varchar(64),
    function_role varchar(64),
    location varchar(256),
    gender varchar(32),
    date_of_birth date,
    blood_group varchar(32),
    father_mother_name varchar(256),
    spouse_name varchar(256),
    address varchar(256),
    mobile varchar(32),
    email varchar(64),
    pan varchar(32),
    aadhar varchar(32),
    uan varchar(32),
    pf_number varchar(32),
    pf_joining_date date,
    pf_relieving_date date,
    pr_account_number varchar(32)
);
CREATE TABLE tally.mst_payhead (
    source_id INTEGER NOT NULL,
    guid varchar(64) not null,
    alterid int,
    name varchar(1024),
    parent varchar(1024),
    _parent varchar(64),
    payslip_name varchar(1024),
    pay_type varchar(64),
    income_type varchar(64),
    calculation_type varchar(32),
    leave_type varchar(64),
    calculation_period varchar(32)
);
CREATE TABLE tally.mst_gst_effective_rate (
    source_id INTEGER NOT NULL,
    item varchar(1024),
    _item varchar(64),
    applicable_from date,
    hsn_description varchar(256),
    hsn_code varchar(64),
    duty_head varchar(64),
    rate decimal(9,4),
    rate_per_unit decimal(9,4),
    valuation_type varchar(64),
    is_rcm_applicable tinyint,
    nature_of_transaction varchar(64),
    nature_of_goods varchar(64),
    supply_type varchar(64),
    taxability varchar(64)
);
CREATE TABLE tally.mst_opening_batch_allocation (
    source_id INTEGER NOT NULL,
    name varchar(1024),
    item varchar(1024),
    _item varchar(64),
    opening_balance decimal(15,4),
    opening_rate decimal(15,4),
    opening_value decimal(17,2),
    godown varchar(1024),
    _godown varchar(64),
    manufactured_on date
);
CREATE TABLE tally.mst_opening_bill_allocation (
    source_id INTEGER NOT NULL,
    ledger varchar(1024),
    _ledger varchar(64),
    opening_balance decimal(17,4),
    bill_date date,
    name varchar(1024),
    bill_credit_period int,
    is_advance tinyint
);
CREATE TABLE tally.trn_closingstock_ledger (
    source_id INTEGER NOT NULL,
    ledger varchar(1024),
    _ledger varchar(64),
    stock_date date,
    stock_value decimal(17,2)
);
CREATE TABLE tally.mst_stockitem_standard_cost (
    source_id INTEGER NOT NULL,
    item varchar(1024),
    _item varchar(64),
    date date,
    rate decimal(15,4)
);
CREATE TABLE tally.mst_stockitem_standard_price (
    source_id INTEGER NOT NULL,
    item varchar(1024),
    _item varchar(64),
    date date,
    rate decimal(15,4)
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
CREATE TABLE tally.trn_cost_category_centre (
    source_id INTEGER NOT NULL,
    guid varchar(64),
    ledger varchar(1024),
    _ledger varchar(64),
    costcategory varchar(1024),
    _costcategory varchar(64),
    costcentre varchar(1024),
    _costcentre varchar(64),
    amount decimal(17,2)
);
CREATE TABLE tally.trn_cost_inventory_category_centre (
    source_id INTEGER NOT NULL,
    guid varchar(64),
    ledger varchar(1024),
    _ledger varchar(64),
    item varchar(1024),
    _item varchar(64),
    costcategory varchar(1024),
    _costcategory varchar(64),
    costcentre varchar(1024),
    _costcentre varchar(64),
    amount decimal(17,2)
);
CREATE TABLE tally.trn_bill (
    source_id INTEGER NOT NULL,
    guid varchar(64),
    ledger varchar(1024),
    _ledger varchar(64),
    name varchar(1024),
    amount decimal(17,2),
    billtype varchar(256),
    bill_credit_period int
);
CREATE TABLE tally.trn_bank (
    source_id INTEGER NOT NULL,
    guid varchar(64),
    ledger varchar(1024),
    _ledger varchar(64),
    transaction_type varchar(32),
    instrument_date date,
    instrument_number varchar(1024),
    bank_name varchar(64),
    amount decimal(17,2),
    bankers_date date
);
CREATE TABLE tally.trn_batch (
    source_id INTEGER NOT NULL,
    guid varchar(64),
    item varchar(1024),
    _item varchar(64),
    name varchar(1024),
    quantity decimal(15,4),
    amount decimal(17,2),
    godown varchar(1024),
    _godown varchar(64),
    destination_godown varchar(1024),
    _destination_godown varchar(64),
    tracking_number varchar(1024)
);
CREATE TABLE tally.trn_inventory_additional_cost (
    source_id INTEGER NOT NULL,
    guid varchar(64),
    ledger varchar(1024),
    _ledger varchar(64),
    amount decimal(17,2),
    additional_allocation_type varchar(32),
    rate_of_invoice_tax decimal(9,4)
);
CREATE TABLE tally.trn_employee (
    source_id INTEGER NOT NULL,
    guid varchar(64),
    category varchar(1024),
    _category varchar(64),
    employee_name varchar(1024),
    _employee_name varchar(64),
    amount decimal(17,2),
    employee_sort_order int
);
CREATE TABLE tally.trn_payhead (
    source_id INTEGER NOT NULL,
    guid varchar(64),
    category varchar(1024),
    _category varchar(64),
    employee_name varchar(1024),
    _employee_name varchar(64),
    employee_sort_order int,
    payhead_name varchar(1024),
    _payhead_name varchar(64),
    payhead_sort_order int,
    amount decimal(17,2)
);
CREATE TABLE tally.trn_attendance (
    source_id INTEGER NOT NULL,
    guid varchar(64),
    employee_name varchar(1024),
    _employee_name varchar(64),
    attendancetype_name varchar(1024),
    _attendancetype_name varchar(64),
    time_value decimal(17,2),
    type_value decimal(17,2)
);
