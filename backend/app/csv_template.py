"""
The standard CSV template and the checks run when it is uploaded.

One row per Salesforce account:

    account_id,name,email,role,country,industry,account_owner,amount,status
    0015g00000AbCdEAAV,Acme GmbH,anna@acme.com,Buyer,,Banking,,,
    0015g00000AbCdFAAV,Globex AG,bo@globex.com,CFO,Austria,,,15500.00,

- account_id, name, email, role identify the record and must be filled in.
- country, industry, account_owner, amount, status are the only fields that can
  be changed. An empty cell means "don't change this field".
- Any other column is rejected.

`parse_csv()` never raises for bad content: it returns the clean rows plus a
list of every problem found, so the user can fix the whole file in one go.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

from schemas import CsvIssue

# ---------------------------------------------------------------------------
# Template definition
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = ["account_id", "name", "email", "role"]
CHANGEABLE_COLUMNS = ["country", "industry", "account_owner", "amount", "status"]
TEMPLATE_COLUMNS = REQUIRED_COLUMNS + CHANGEABLE_COLUMNS

MAX_ROWS = 500
MAX_FILE_BYTES = 1_000_000  # 1 MB

# Where each changeable column will be written in Salesforce.
# These are educated guesses: confirm them with your Salesforce admin, then this
# is the only place to change when you connect Salesforce.
SALESFORCE_FIELD_MAP = {
    "country": "Account.BillingCountry",
    "industry": "Account.Industry",
    "account_owner": "Account.OwnerId",   # Salesforce stores the owner as a user ID
    "amount": "Opportunity.Amount",       # TODO(salesforce): amount usually lives on an Opportunity
    "status": "Account.Status__c",        # TODO(salesforce): no standard Account status; likely a custom field
}

# Salesforce Account IDs are 15 or 18 letters/digits and start with "001".
ACCOUNT_ID_PREFIX = "001"

# Salesforce's standard Industry picklist. Replace with your org's values if they're customised.
INDUSTRY_VALUES = [
    "Agriculture", "Apparel", "Banking", "Biotechnology", "Chemicals", "Communications",
    "Construction", "Consulting", "Education", "Electronics", "Energy", "Engineering",
    "Entertainment", "Environmental", "Finance", "Food & Beverage", "Government",
    "Healthcare", "Hospitality", "Insurance", "Machinery", "Manufacturing", "Media",
    "Not For Profit", "Other", "Recreation", "Retail", "Shipping", "Technology",
    "Telecommunications", "Transportation", "Utilities",
]


# ---------------------------------------------------------------------------
# Value checks: each returns an error message, or None when the value is fine
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r"^[a-zA-Z0-9]{15}([a-zA-Z0-9]{3})?$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_INDUSTRY_LOOKUP = {v.lower(): v for v in INDUSTRY_VALUES}


def check_account_id(value: str) -> Optional[str]:
    if not _ID_RE.match(value):
        return (f"'{value}' is not a valid Salesforce ID. Copy the 15- or 18-character "
                "ID from the account's page in Salesforce.")
    if not value.startswith(ACCOUNT_ID_PREFIX):
        return f"'{value}' is not an Account ID (Account IDs start with {ACCOUNT_ID_PREFIX})."
    return None


def check_email(value: str) -> Optional[str]:
    return None if _EMAIL_RE.match(value) else f"'{value}' is not a valid email address."


def check_max_length(max_length: int) -> Callable[[str], Optional[str]]:
    def check(value: str) -> Optional[str]:
        return f"Value is longer than {max_length} characters." if len(value) > max_length else None
    return check


def check_industry(value: str) -> Optional[str]:
    if value.lower() not in _INDUSTRY_LOOKUP:
        return f"'{value}' is not an allowed industry. Allowed: {', '.join(INDUSTRY_VALUES)}"
    return None


def check_amount(value: str) -> Optional[str]:
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return f"'{value}' is not a number. Use digits and a dot, e.g. 12500.00 (no currency sign or thousands separator)."
    if not amount.is_finite():
        return f"'{value}' is not a number."
    if amount < 0:
        return "Amount cannot be negative."
    if amount.as_tuple().exponent < -2:
        return "Amount can have at most 2 decimal places."
    if abs(amount) >= Decimal("1e16"):
        return "Amount is too large."
    return None


CHECKS: dict[str, Callable[[str], Optional[str]]] = {
    "account_id": check_account_id,
    "name": check_max_length(255),
    "email": check_email,
    "role": check_max_length(100),
    "country": check_max_length(80),
    "industry": check_industry,
    "account_owner": check_max_length(200),
    "amount": check_amount,
    "status": check_max_length(100),
}


# ---------------------------------------------------------------------------
# Template download
# ---------------------------------------------------------------------------


def template_csv() -> str:
    """The blank template with example rows. Empty cells mean 'no change'."""
    examples = [
        ["0015g00000AbCdEAAV", "Acme GmbH", "anna@acme.com", "Buyer", "", "Banking", "", "", ""],
        ["0015g00000AbCdFAAV", "Globex AG", "bo@globex.com", "CFO", "Austria", "", "", "15500.00", ""],
        ["0015g00000AbCdGAAV", "Initech", "carl@initech.com", "IT Manager", "", "", "Jane Smith", "", "Active"],
    ]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(TEMPLATE_COLUMNS)
    writer.writerows(examples)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Parsing + checks
# ---------------------------------------------------------------------------


@dataclass
class ParsedRow:
    row_number: int
    salesforce_account_id: str
    name: str
    email: str
    role: str
    country: Optional[str]
    industry: Optional[str]
    account_owner: Optional[str]
    amount: Optional[Decimal]
    record_status: Optional[str]

    def changed_fields(self) -> list[str]:
        values = {"country": self.country, "industry": self.industry,
                  "account_owner": self.account_owner, "amount": self.amount,
                  "status": self.record_status}
        return [col for col in CHANGEABLE_COLUMNS if values[col] is not None]


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):  # utf-8-sig strips Excel's BOM
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("unreadable")


def _sniff_delimiter(text: str) -> str:
    """Excel in many European locales (e.g. German) saves CSV with ';' instead of ','."""
    first_line = text.splitlines()[0] if text else ""
    return ";" if first_line.count(";") > first_line.count(",") else ","


def parse_csv(raw: bytes) -> tuple[list[ParsedRow], list[CsvIssue], int]:
    """Returns (valid rows, issues, number of data rows in the file).

    The file is only usable when `issues` is empty.
    """
    issues: list[CsvIssue] = []

    def file_error(code: str, message: str) -> tuple[list[ParsedRow], list[CsvIssue], int]:
        return [], [CsvIssue(code=code, message=message)], 0

    # --- file level -------------------------------------------------------
    if not raw.strip():
        return file_error("empty_file", "The file is empty.")
    if len(raw) > MAX_FILE_BYTES:
        return file_error("file_too_large", f"The file is larger than {MAX_FILE_BYTES // 1000} KB.")
    try:
        text = _decode(raw)
    except ValueError:
        return file_error("bad_encoding", "The file could not be read. Save it as CSV (UTF-8).")

    reader = csv.reader(io.StringIO(text), delimiter=_sniff_delimiter(text))
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        return file_error("bad_csv", "The file is not a valid CSV.")

    # --- header -----------------------------------------------------------
    columns = [h.strip().lower() for h in header]
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    unknown = [header[i].strip() for i, c in enumerate(columns) if c and c not in TEMPLATE_COLUMNS]
    dupes = sorted({c for c in columns if c and columns.count(c) > 1})
    if missing:
        issues.append(CsvIssue(code="missing_column",
                               message=f"Missing mandatory column(s): {', '.join(missing)}."))
    if unknown:
        issues.append(CsvIssue(code="unknown_column",
                               message=f"Column(s) not allowed: {', '.join(unknown)}. Only "
                                       f"{', '.join(CHANGEABLE_COLUMNS)} can be changed."))
    if dupes:
        issues.append(CsvIssue(code="duplicate_column",
                               message=f"Column(s) appear more than once: {', '.join(dupes)}."))
    if issues:
        return [], issues, 0
    index = {c: columns.index(c) for c in TEMPLATE_COLUMNS if c in columns}

    # --- rows -------------------------------------------------------------
    rows: list[ParsedRow] = []
    first_seen: dict[str, int] = {}  # account id -> line it first appeared on
    data_rows = 0
    try:
        for line_no, values in enumerate(reader, start=2):
            if not any(v.strip() for v in values):
                continue  # skip blank lines
            data_rows += 1
            if data_rows > MAX_ROWS:
                issues.append(CsvIssue(code="too_many_rows",
                                       message=f"The file has more than {MAX_ROWS} rows. "
                                               "Split it into several requests."))
                break
            if len(values) > len(header):
                issues.append(CsvIssue(row=line_no, code="extra_values",
                                       message=f"Row has {len(values)} values but the header has "
                                               f"{len(header)} columns. Check for unquoted commas."))
                continue
            row = _check_row(line_no, values, index, issues)
            if row is None:
                continue
            # Salesforce IDs: the first 15 characters identify the record.
            key = row.salesforce_account_id[:15]
            if key in first_seen:
                issues.append(CsvIssue(row=line_no, column="account_id", code="duplicate_account",
                                       message=f"Account {row.salesforce_account_id} is already on line "
                                               f"{first_seen[key]}. Put all changes for one account in one row."))
                continue
            first_seen[key] = line_no
            rows.append(row)
    except csv.Error as exc:
        issues.append(CsvIssue(code="bad_csv", message=f"The CSV is malformed: {exc}"))

    if data_rows == 0 and not issues:
        issues.append(CsvIssue(code="no_rows", message="The file has a header but no account rows."))
    return rows, issues, data_rows


def _check_row(line_no: int, values: list[str], index: dict[str, int],
               issues: list[CsvIssue]) -> Optional[ParsedRow]:
    def get(col: str) -> str:
        i = index.get(col)
        return values[i].strip() if i is not None and i < len(values) else ""

    def err(column: Optional[str], code: str, message: str) -> None:
        issues.append(CsvIssue(row=line_no, column=column, code=code, message=message))

    before = len(issues)

    for col in REQUIRED_COLUMNS:
        if not get(col):
            err(col, "missing_value", f"'{col}' is mandatory.")
        elif msg := CHECKS[col](get(col)):
            err(col, "invalid_value", msg)

    changed = [col for col in CHANGEABLE_COLUMNS if get(col)]
    for col in changed:
        if msg := CHECKS[col](get(col)):
            err(col, "invalid_value", msg)
    if not changed:
        err(None, "no_changes",
            f"This row doesn't change anything. Fill in at least one of: {', '.join(CHANGEABLE_COLUMNS)}.")

    if len(issues) > before:
        return None
    return ParsedRow(
        row_number=line_no,
        salesforce_account_id=get("account_id"),
        name=get("name"),
        email=get("email"),
        role=get("role"),
        country=get("country") or None,
        industry=_INDUSTRY_LOOKUP[get("industry").lower()] if get("industry") else None,
        account_owner=get("account_owner") or None,
        amount=Decimal(get("amount")).quantize(Decimal("0.01")) if get("amount") else None,
        record_status=get("status") or None,
    )


def summarise(rows: list[ParsedRow]) -> str:
    """Short description for change_requests.requested_change, e.g.
    '3 accounts: industry (2), amount (1), country (1)'."""
    counts: dict[str, int] = {}
    for row in rows:
        for col in row.changed_fields():
            counts[col] = counts.get(col, 0) + 1
    parts = ", ".join(f"{col} ({n})" for col, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    noun = "account" if len(rows) == 1 else "accounts"
    return f"{len(rows)} {noun}: {parts}"