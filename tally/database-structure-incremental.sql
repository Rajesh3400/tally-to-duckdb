-- TRIMMED from the upstream Tally Database Loader definition: only the 9 collections tally-to-duckdb
-- reads are kept (mst_group, mst_ledger, mst_vouchertype, mst_cost_centre, mst_stock_item, trn_voucher,
-- trn_accounting, trn_cost_centre, trn_inventory). Removed (23):
-- mst_uom, mst_godown, mst_stock_category, mst_stock_group,
-- mst_cost_category, mst_attendance_type, mst_employee, mst_payhead,
-- mst_gst_effective_rate, mst_opening_batch_allocation,
-- mst_opening_bill_allocation, trn_closingstock_ledger,
-- mst_stockitem_standard_cost, mst_stockitem_standard_price,
-- trn_cost_category_centre, trn_cost_inventory_category_centre, trn_bill,
-- trn_bank, trn_batch, trn_inventory_additional_cost, trn_employee,
-- trn_payhead, trn_attendance
-- Cascade rules that pointed at a removed table were dropped. See README: add a collection back.
create table _diff
(
 guid varchar(64) not null,
 alterid int
);

create table _delete
(
 guid varchar(64) not null
);

create table _vchnumber
(
 guid varchar(64) not null,
 voucher_number varchar(256)
);

create table config
(
 name nvarchar(64) not null primary key,
 value nvarchar(1024)
);

create table mst_group
(
 guid varchar(64) not null primary key,
 alterid int,
 name nvarchar(1024),
 parent nvarchar(1024),
 _parent varchar(64),
 primary_group nvarchar(1024),
 is_revenue tinyint,
 is_deemedpositive tinyint,
 is_reserved tinyint,
 affects_gross_profit tinyint,
 sort_position int
);

create table mst_ledger
(
 guid varchar(64) not null primary key,
 alterid int,
 name nvarchar(1024),
 parent nvarchar(1024),
 _parent varchar(64),
 alias nvarchar(256),
 description nvarchar(64),
 notes nvarchar(64),
 is_revenue tinyint,
 is_deemedpositive tinyint,
 opening_balance decimal(17,2),
 closing_balance decimal(17,2),
 mailing_name nvarchar(256),
 mailing_address nvarchar(1024),
 mailing_state nvarchar(256),
 mailing_country nvarchar(256),
 mailing_pincode nvarchar(64),
 email nvarchar(256),
 mobile nvarchar(32),
 it_pan nvarchar(64),
 gstn nvarchar(64),
 gst_registration_type nvarchar(64),
 gst_supply_type nvarchar(64),
 gst_duty_head nvarchar(16),
 bank_account_holder nvarchar(256),
 bank_account_number nvarchar(64),
 bank_ifsc nvarchar(64),
 bank_swift nvarchar(64),
 bank_name nvarchar(64),
 bank_branch nvarchar(64),
 bill_credit_period int
);

create table mst_vouchertype
(
 guid varchar(64) not null primary key,
 alterid int,
 name nvarchar(1024),
 parent nvarchar(1024),
 _parent varchar(64),
 numbering_method nvarchar(64),
 is_deemedpositive tinyint,
 affects_stock tinyint
);

create table mst_stock_item
(
 guid varchar(64) not null primary key,
 alterid int,
 name nvarchar(1024),
 parent nvarchar(1024),
 _parent varchar(64),
 category nvarchar(1024),
 _category varchar(64),
 alias nvarchar(256),
 description nvarchar(64),
 notes nvarchar(64),
 part_number nvarchar(256),
 uom nvarchar(32),
 _uom varchar(64),
 alternate_uom nvarchar(32),
 _alternate_uom varchar(64),
 conversion decimal(15,4),
 opening_balance decimal(15,4),
 opening_rate decimal(15,4),
 opening_value decimal(17,2),
 closing_balance decimal(15,4),
 closing_rate decimal(15,4),
 closing_value decimal(17,2),
 costing_method nvarchar(32),
 gst_type_of_supply nvarchar(32),
 gst_hsn_code nvarchar(64),
 gst_hsn_description nvarchar(256),
 gst_rate decimal(9,4),
 gst_taxability nvarchar(32)
);

create table mst_cost_centre
(
 guid varchar(64) not null primary key,
 alterid int,
 name nvarchar(1024),
 parent nvarchar(1024),
 _parent varchar(64),
 category nvarchar(1024)
);

create table trn_voucher
(
 guid varchar(64) not null primary key,
 alterid int,
 date date,
 voucher_type nvarchar(1024),
 _voucher_type varchar(64),
 voucher_number nvarchar(64),
 reference_number nvarchar(64),
 reference_date date,
 narration nvarchar(4000),
 party_name nvarchar(256),
 _party_name varchar(64),
 place_of_supply nvarchar(256),
 is_invoice tinyint,
 is_accounting_voucher tinyint,
 is_inventory_voucher tinyint,
 is_order_voucher tinyint
);

create table trn_accounting
(
 guid varchar(64),
 ledger nvarchar(1024),
 _ledger varchar(64),
 amount decimal(17,2),
 amount_forex decimal(17,2),
 currency nvarchar(16)
);

create table trn_inventory
(
 guid varchar(64),
 item nvarchar(1024),
 _item varchar(64),
 quantity decimal(15,4),
 rate decimal(15,4),
 amount decimal(17,2),
 additional_amount decimal(17,2),
 discount_amount decimal(17,2),
 godown nvarchar(1024),
 _godown varchar(64),
 tracking_number nvarchar(256),
 order_number nvarchar(256),
 order_duedate date
);

create table trn_cost_centre
(
 guid varchar(64),
 ledger nvarchar(1024),
 _ledger varchar(64),
 costcentre nvarchar(1024),
 _costcentre varchar(64),
 amount decimal(17,2)
);
