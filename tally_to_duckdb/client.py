"""Talking to Tally's XML gateway. A line-for-line port of the request logic of
the open-source Tally Database Loader 1.0.44 (see NOTICE). The export
definition (tally/tally-export-config-incremental.yaml) is the loader's,
unchanged.

Only the network side lives here; sync.py does the database side.
"""
from __future__ import annotations

import base64
import datetime as dt
import http.client
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from tally_to_duckdb.config import data_dir

DEFINITION = data_dir("tally") / "tally-export-config-incremental.yaml"
BATCH_SIZE = 5000      # vouchers per request window - a whole-year pull once hung Tally
NULL_MARK = "ñ"        # Tally's "empty date" sentinel ($$StrByCharCode:241)
TIMEOUT = 1800         # one windowed request can legitimately take minutes


class TallyError(Exception):
    pass


class TallyIncomplete(TallyError):
    """Tally's answer did not end properly (cut off) - ask again."""


@dataclass
class TallyConn:
    host: str
    port: int
    protocol: str = "http"
    basic_auth_user: str = ""
    basic_auth_pass: str = ""


def post(conn: TallyConn, xml: str, timeout: float = TIMEOUT) -> str:
    """UTF-16LE body and content type, exactly as the loader sent it."""
    body = xml.encode("utf-16-le")
    headers = {"Content-Type": "text/xml;charset=utf-16", "Content-Length": str(len(body))}
    if conn.basic_auth_user:
        token = base64.b64encode(f"{conn.basic_auth_user}:{conn.basic_auth_pass}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    cls = http.client.HTTPSConnection if conn.protocol == "https" else http.client.HTTPConnection
    c = cls(conn.host, conn.port, timeout=timeout)
    try:
        try:
            c.request("POST", "/", body=body, headers=headers)
            r = c.getresponse()
            raw = r.read()
        except OSError as exc:
            raise TallyError(f"Cannot reach Tally at {conn.protocol}://{conn.host}:{conn.port} ({exc})") from exc
        if r.status != 200:
            raise TallyError(f"Tally answered HTTP {r.status} {r.reason}")
    finally:
        c.close()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    # Tally answers in the request's encoding; a UTF-16 body has NULs every other byte.
    return raw.decode("utf-16-le") if raw[1:2] == b"\x00" else raw.decode("utf-8", errors="replace")


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")


def open_companies(conn: TallyConn) -> list[str]:
    """Names of the companies loaded in Tally right now, Tally's exact spelling
    (a source must match it character for character)."""
    out = post(conn, "<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE>"
                     "<ID>List of Companies</ID></HEADER><BODY><DESC><STATICVARIABLES>"
                     "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES></DESC></BODY></ENVELOPE>", timeout=30)
    names = re.findall(r'<COMPANY NAME="([^"]+)"', out)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        names = [n.replace(a, b) for n in names]
    return names


def set_company(conn: TallyConn, company: str) -> None:
    post(conn, '<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE>'
               '<ID>MyReport</ID></HEADER><BODY><DESC><TDL><TDLMESSAGE><REPORT NAME="MyReport">'
               f'<USE>ChangeCurrentCompany</USE><SET>SVCurrentCompany : "{esc(company)}"</SET></REPORT>'
               '</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>')


@dataclass
class CompanyInfo:
    name: str
    books_from: dt.date
    last_voucher_date: dt.date
    alterid_master: int
    alterid_transaction: int


def company_info(conn: TallyConn, company: str) -> CompanyInfo:
    """The loader's saveCompanyInfo request. Raises when the company is not open."""
    xml = ('<?xml version="1.0" encoding="utf-8"?><ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>'
           '<TYPE>Data</TYPE><ID>TallyDatabaseLoaderReport</ID></HEADER><BODY><DESC><STATICVARIABLES>'
           '<SVEXPORTFORMAT>ASCII (Comma Delimited)</SVEXPORTFORMAT></STATICVARIABLES><TDL><TDLMESSAGE>'
           '<REPORT NAME="TallyDatabaseLoaderReport"><FORMS>MyForm</FORMS></REPORT><FORM NAME="MyForm"><PARTS>MyPart</PARTS></FORM>'
           '<PART NAME="MyPart"><LINES>MyLine</LINES><REPEAT>MyLine : MyCollection</REPEAT><SCROLLED>Vertical</SCROLLED></PART>'
           '<LINE NAME="MyLine"><FIELDS>FldGuid,FldName,FldBooksFrom,FldLastVoucherDate,FldLastAlterIdMaster,FldLastAlterIdTransaction,FldEOL</FIELDS></LINE>'
           '<FIELD NAME="FldGuid"><SET>$Guid</SET></FIELD><FIELD NAME="FldName"><SET>$$StringFindAndReplace:$Name:\'"\':\'""\'</SET></FIELD>'
           '<FIELD NAME="FldBooksFrom"><SET>(($$YearOfDate:$BooksFrom)*10000)+(($$MonthOfDate:$BooksFrom)*100)+(($$DayOfDate:$BooksFrom)*1)</SET></FIELD>'
           '<FIELD NAME="FldLastVoucherDate"><SET>(($$YearOfDate:$LastVoucherDate)*10000)+(($$MonthOfDate:$LastVoucherDate)*100)+(($$DayOfDate:$LastVoucherDate)*1)</SET></FIELD>'
           '<FIELD NAME="FldLastAlterIdMaster"><SET>$AltMstId</SET></FIELD><FIELD NAME="FldLastAlterIdTransaction"><SET>$AltVchId</SET></FIELD>'
           '<FIELD NAME="FldEOL"><SET>†</SET></FIELD><COLLECTION NAME="MyCollection"><TYPE>Company</TYPE>'
           '<FILTER>FilterActiveCompany</FILTER></COLLECTION><SYSTEM TYPE="Formulae" NAME="FilterActiveCompany">'
           f'$$IsEqual:"{esc(company)}":$Name</SYSTEM></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>')
    out = post(conn, xml).strip()
    if '"†"' not in out:
        raise TallyError(f'Company "{company}" is not open in Tally')
    parts = [p.strip().strip('"') for p in out.split('","')]

    def ymd(s: str) -> dt.date:
        s = s.strip()
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))

    return CompanyInfo(parts[1], ymd(parts[2]), ymd(parts[3]), int(parts[4]), int(parts[5]))


def load_definition() -> tuple[list[dict], list[dict]]:
    d = yaml.safe_load(Path(DEFINITION).read_text(encoding="utf-8"))
    return d["master"], d["transaction"]


def _fmt_date(d: dt.date) -> str:
    return f"{d.day}-{d.strftime('%b')}-{d.year}"


def report_xml(table: dict, company: str, from_date: dt.date, to_date: dt.date) -> str:
    """generateXMLfromYAML (loader tally.mjs), line for line."""
    x = ('<?xml version="1.0" encoding="utf-8"?><ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>'
         '<TYPE>Data</TYPE><ID>TallyDatabaseLoaderReport</ID></HEADER><BODY><DESC><STATICVARIABLES>'
         '<SVEXPORTFORMAT>XML (Data Interchange)</SVEXPORTFORMAT>'
         f'<SVFROMDATE>{_fmt_date(from_date)}</SVFROMDATE><SVTODATE>{_fmt_date(to_date)}</SVTODATE>'
         f'<SVCURRENTCOMPANY>{esc(company)}</SVCURRENTCOMPANY></STATICVARIABLES><TDL><TDLMESSAGE>'
         '<REPORT NAME="TallyDatabaseLoaderReport"><FORMS>MyForm</FORMS></REPORT><FORM NAME="MyForm"><PARTS>MyPart01</PARTS></FORM>')
    routes = table["collection"].split(".")
    target_collection = routes.pop(0)
    routes.insert(0, "MyCollection")
    for i, route in enumerate(routes):
        x += (f'<PART NAME="MyPart{i + 1:02d}"><LINES>MyLine{i + 1:02d}</LINES>'
              f'<REPEAT>MyLine{i + 1:02d} : {route}</REPEAT><SCROLLED>Vertical</SCROLLED></PART>')
    for i in range(len(routes) - 1):
        x += f'<LINE NAME="MyLine{i + 1:02d}"><FIELDS>FldBlank</FIELDS><EXPLODE>MyPart{i + 2:02d}</EXPLODE></LINE>'
    fields = table["fields"]
    x += f'<LINE NAME="MyLine{len(routes):02d}"><FIELDS>' + ",".join(f"Fld{i + 1:02d}" for i in range(len(fields))) + "</FIELDS></LINE>"
    for i, f in enumerate(fields):
        expr, ftype = f["field"], f["type"]
        s = expr
        if re.fullmatch(r"(\.\.)?[a-zA-Z0-9_]+", expr):
            s = {
                "text": f"${expr}",
                "logical": f"if ${expr} then 1 else 0",
                "date": f'if $$IsEmpty:${expr} then $$StrByCharCode:241 else $$PyrlYYYYMMDDFormat:${expr}:"-"',
                "number": f'if $$IsEmpty:${expr} then "0" else $$StringFindAndReplace:($$String:${expr}):"(-)":"-"',
                "amount": f'$$StringFindAndReplace:(if $$IsDebit:${expr} then -$$NumValue:${expr} else $$NumValue:${expr}):"(-)":"-"',
                "quantity": (f'$$StringFindAndReplace:(if $$IsInwards:${expr} then $$Number:$$String:${expr}:"TailUnits" '
                             f'else -$$Number:$$String:${expr}:"TailUnits"):"(-)":"-"'),
                "rate": f"if $$IsEmpty:${expr} then 0 else $$Number:${expr}",
            }.get(ftype, expr)
        x += f'<FIELD NAME="Fld{i + 1:02d}"><SET>{s}</SET><XMLTAG>F{i + 1:02d}</XMLTAG></FIELD>'
    x += '<FIELD NAME="FldBlank"><SET>""</SET></FIELD>'
    x += f'<COLLECTION NAME="MyCollection"><TYPE>{target_collection}</TYPE>'
    if table.get("fetch"):
        x += f"<FETCH>{','.join(table['fetch'])}</FETCH>"
    filters = table.get("filters") or []
    if filters:
        x += "<FILTER>" + ",".join(f"Fltr{j + 1:02d}" for j in range(len(filters))) + "</FILTER>"
    x += "</COLLECTION>"
    for j, flt in enumerate(filters):
        x += f'<SYSTEM TYPE="Formulae" NAME="Fltr{j + 1:02d}">{flt}</SYSTEM>'
    return x + "</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"


_ENTITY = re.compile(r"&#\d+;")


def flatten(txt: str) -> str:
    """processTdlOutputManipulation (loader tally.mjs): <F01>..<Fnn> XML -> tab-separated
    lines. Tally writes no tabs/newlines inside values, so a line is a row."""
    t = txt.replace("<ENVELOPE>", "", 1).replace("</ENVELOPE>", "", 1)
    t = t.replace("<FLDBLANK></FLDBLANK>", "")
    t = re.sub(r"\s+\r\n", "", t)
    t = t.replace("\r\n", "").replace("\t", " ")
    t = re.sub(r"\s+<F", "<F", t)
    t = re.sub(r"</F\d+>", "", t)
    t = t.replace("<F01>", "\r\n")
    t = re.sub(r"<F\d+>", "\t", t)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&tab;", "")):
        t = t.replace(a, b)
    return _ENTITY.sub("", t)


def fetch(conn: TallyConn, table: dict, company: str, from_date: dt.date, to_date: dt.date) -> str:
    """One report as TSV text with a header line. An answer that does not end
    with </ENVELOPE> was cut off: TallyIncomplete, so the caller asks again
    (this Tally returned short and empty answers under load)."""
    raw = post(conn, report_xml(table, company, from_date, to_date))
    if not raw.rstrip().endswith("</ENVELOPE>"):
        raise TallyIncomplete(f"{table.get('collection')}: Tally's answer was cut off ({len(raw):,} chars)")
    return "\t".join(f["name"] for f in table["fields"]) + flatten(raw)


@dataclass
class Windows:
    """Date windows for batching a transaction pull, and how many vouchers
    Tally said there are in total - the count a FULL pull must reproduce."""
    windows: list[tuple[dt.date, dt.date]]
    vouchers: int
    counts: list[int] | None = None   # vouchers Tally counted in each window (dated ones)
    last_date: dt.date | None = None  # latest voucher date in the list - must equal Tally's own LastVoucherDate


def date_windows(conn: TallyConn, company: str, from_date: dt.date, to_date: dt.date,
                 extra_filters: list[str] | None = None) -> Windows:
    """Windows of <= BATCH_SIZE vouchers (computeTransactionDateWindows,
    loader tally.mjs), CONTIGUOUS and covering from_date..to_date exactly: each
    window ends the day before the next begins, the first starts at from_date,
    the last ends at to_date - so no voucher can fall between two windows
    (e.g. one keyed in on a quiet day while the sync runs)."""
    probe = {"collection": "Voucher", "filters": ["NOT $IsCancelled", "NOT $IsOptional", *(extra_filters or [])],
             "fields": [{"name": "date", "field": "Date", "type": "date"}]}
    counts: dict[dt.date, int] = {}
    total = 0
    for line in fetch(conn, probe, company, from_date, to_date).split("\r\n")[1:]:
        d = line.strip()
        if not d:
            continue
        total += 1          # a date-less voucher counts too: a windowed pull would miss it, and the count check must see that
        if d != NULL_MARK:
            day = dt.date.fromisoformat(d)
            counts[day] = counts.get(day, 0) + 1
    batches: list[list] = []
    for day in sorted(counts):
        if batches and batches[-1][1] + counts[day] <= BATCH_SIZE:
            batches[-1][1] += counts[day]
        else:
            batches.append([day, counts[day]])
    last = max(counts) if counts else None
    if len(batches) <= 1:
        return Windows([(from_date, to_date)], total, [total], last)
    starts = [from_date] + [b[0] for b in batches[1:]]
    ends = [s - dt.timedelta(days=1) for s in starts[1:]] + [to_date]
    # Per-window counts from the DATED vouchers; a date-less one (in `total`)
    # matches no window, so the total count check catches it.
    return Windows(list(zip(starts, ends)), total, [b[1] for b in batches], last)


def window_filter(lo: dt.date, hi: dt.date) -> str:
    """A TDL filter keeping vouchers dated lo..hi (inclusive) - REQUIRED for a
    windowed pull. Tally IGNORES SVFROMDATE/SVTODATE for these collections
    (observed: a 2-day request returned every voucher of
    the year), so without this every window re-fetched the whole year and a
    full sync stored each voucher 4-10 times. A date-LITERAL comparison
    ($Date <= $$Date:"...") returned nothing at all in the same Tally; the
    date compared as a yyyymmdd NUMBER is exact (exactly the vouchers of those two days).
    Applies to every transaction table: all are collections under Voucher."""
    num = "(($$YearOfDate:$Date)*10000+($$MonthOfDate:$Date)*100+$$DayOfDate:$Date)"
    return f"{num} &gt;= {lo:%Y%m%d} AND {num} &lt;= {hi:%Y%m%d}"


def closings(conn: TallyConn, company: str, from_date: dt.date, to_date: dt.date) -> str:
    """EVERY ledger's closing balance and every stock item's closing value, as
    Tally computes them for the period - the independent side of the tie-out.
    Fetched in full each sync (see schema 002's tally_closing for why).
    Returned as TSV (kind, guid, closing) for the same file-based staging as
    every other table."""
    lines = ["kind\tguid\tclosing"]
    for kind, collection, field in (("LEDGER", "Ledger", "ClosingBalance"), ("STOCK", "StockItem", "ClosingValue")):
        spec = {"collection": collection,
                "fields": [{"name": "guid", "field": "Guid", "type": "text"},
                           {"name": "closing", "field": field, "type": "amount"}]}
        for line in fetch(conn, spec, company, from_date, to_date).split("\r\n")[1:]:
            if line.strip():
                guid, value = line.split("\t")[:2]
                lines.append(f"{kind}\t{guid}\t{value.strip() or '0'}")
    return "\r\n".join(lines)
