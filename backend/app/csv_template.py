"""
The standard CSV template and the format checks run when it is uploaded.

One row per change:

    account_id,name,email,role,object,record_id,field,current_value,new_value
    0015g00000AbCdEAAV,Acme GmbH,anna@acme.com,Buyer,Account,0015g00000AbCdEAAV,industry,Retail,Banking
    0015g00000AbCdEAAV,Acme GmbH,anna@acme.com,Buyer,Opportunity,0065g00000LmNoPAAV,amount,12000.00,15500.00

- account_id, name, email, role: the account the change is about.
- object + record_id: the Salesforce record to change (for Account changes,
  record_id is the account itself).
- field: one of the five changeable fields (see ALLOWED_FIELDS).
- current_value: what Salesforce has now. Write (empty) if it's blank.
- new_value: what it should become.

Every column is mandatory and no other columns are allowed.

`parse_csv()` never raises for bad content: it returns the clean rows plus a
list of every problem found, so the user can fix the whole file in one go.
The checks against Salesforce, other requests and the business rules run on
POST /change-requests/{id}/validate (validation.py, business_rules.py).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

from schemas import SalesforceObject, ValidationIssue

# ---------------------------------------------------------------------------
# Template definition
# ---------------------------------------------------------------------------

TEMPLATE_COLUMNS = ["account_id", "name", "email", "role",
                    "object", "record_id", "field", "current_value", "new_value"]

EMPTY_TOKEN = "(empty)"     # write this in current_value when the field is blank in Salesforce
MAX_ROWS = 500
MAX_FILE_BYTES = 1_000_000  # 1 MB

# Salesforce record IDs: the first 3 characters say which object the record is.
ID_PREFIX = {
    SalesforceObject.ACCOUNT: "001",
    SalesforceObject.OPPORTUNITY: "006",
    SalesforceObject.LEAD: "00Q",
}
USER_ID_PREFIX = "005"

# Standard Salesforce picklist values. Replace them if your org has customised them
# (later they can be fetched from Salesforce instead).
INDUSTRY_VALUES = [
    "Agriculture", "Apparel", "Banking", "Biotechnology", "Chemicals", "Communications",
    "Construction", "Consulting", "Education", "Electronics", "Energy", "Engineering",
    "Entertainment", "Environmental", "Finance", "Food & Beverage", "Government",
    "Healthcare", "Hospitality", "Insurance", "Machinery", "Manufacturing", "Media",
    "Not For Profit", "Other", "Recreation", "Retail", "Shipping", "Technology",
    "Telecommunications", "Transportation", "Utilities",
]
LEAD_STATUS_VALUES = [
    "Open - Not Contacted", "Working - Contacted", "Closed - Converted", "Closed - Not Converted",
]


# ---------------------------------------------------------------------------
# Salesforce IDs
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r"^[a-zA-Z0-9]{15}([a-zA-Z0-9]{3})?$")
_SUFFIX_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


def to_18_char_id(sf_id: str) -> str:
    """Convert a 15-character (case-sensitive) Salesforce ID to the 18-character form."""
    base = sf_id[:15]
    suffix = ""
    for i in range(3):
        chunk = base[i * 5:(i + 1) * 5]
        bits = sum(1 << j for j, ch in enumerate(chunk) if ch.isupper())
        suffix += _SUFFIX_CHARS[bits]
    return base + suffix


def _restore_case(id18: str) -> Optional[str]:
    """18-character IDs are case-insensitive: the last 3 characters encode which of the
    first 15 are upper case. Rebuild the exact ID from them, or None if they don't fit."""
    base = list(id18[:15])
    suffix = id18[15:].upper()
    for i, ch in enumerate(suffix):
        bits = _SUFFIX_CHARS.find(ch)
        if bits < 0:
            return None
        for j in range(5):
            pos, upper = i * 5 + j, bool(bits >> j & 1)
            if base[pos].isalpha():
                base[pos] = base[pos].upper() if upper else base[pos].lower()
            elif upper:
                return None  # a digit can't be upper case: the ID was mistyped
    return "".join(base) + suffix


def normalise_sf_id(raw: str) -> Optional[str]:
    """Return the 18-character ID, or None if `raw` is not a valid Salesforce ID."""
    if not _ID_RE.match(raw):
        return None
    return _restore_case(raw) if len(raw) == 18 else to_18_char_id(raw)


def same_sf_id(a: str, b: str) -> bool:
    return to_18_char_id(a).upper() == to_18_char_id(b).upper()


# ---------------------------------------------------------------------------
# Changeable fields and their value rules
# ---------------------------------------------------------------------------

# A value check returns an error message, or None when the value is fine.
ValueCheck = Callable[[str], Optional[str]]


def check_user_id(value: str) -> Optional[str]:
    sf_id = normalise_sf_id(value)
    if sf_id is None:
        return f"'{value}' is not a valid Salesforce user ID. Copy it from the user's page in Salesforce."
    if not sf_id.startswith(USER_ID_PREFIX):
        return f"'{value}' is not a user ID (user IDs start with {USER_ID_PREFIX})."
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
    if amount >= Decimal("1e16"):
        return "Amount is too large."
    return None


def check_text(max_length: int) -> ValueCheck:
    def check(value: str) -> Optional[str]:
        return f"Value is longer than {max_length} characters." if len(value) > max_length else None
    return check


def check_picklist(allowed: list[str]) -> ValueCheck:
    lookup = {v.lower() for v in allowed}

    def check(value: str) -> Optional[str]:
        if value.lower() not in lookup:
            return f"'{value}' is not an allowed value. Allowed: {', '.join(allowed)}"
        return None
    return check


def _picklist_canonical(allowed: list[str]) -> Callable[[str], str]:
    lookup = {v.lower(): v for v in allowed}
    return lambda v: lookup.get(v.lower(), v)


def _user_id_canonical(v: str) -> str:
    return normalise_sf_id(v) or v


def _amount_canonical(v: str) -> str:
    try:
        return f"{Decimal(v):.2f}"
    except InvalidOperation:
        return v


@dataclass(frozen=True)
class FieldRule:
    object_type: SalesforceObject
    salesforce_field: str            # API name in Salesforce
    check: ValueCheck
    canonical: Callable[[str], str] = lambda v: v  # tidy a valid value, e.g. fix picklist casing

    def same(self, a: str, b: str) -> bool:
        """True when two values mean the same thing (e.g. 12000 and 12000.00)."""
        if not a or not b:
            return a == b
        return self.canonical(a).casefold() == self.canonical(b).casefold()


# The only changes allowed. The key is what the user writes in the `field` column.
ALLOWED_FIELDS: dict[str, FieldRule] = {
    "country": FieldRule(SalesforceObject.ACCOUNT, "BillingCountry", check_text(80)),
    "industry": FieldRule(SalesforceObject.ACCOUNT, "Industry",
                          check_picklist(INDUSTRY_VALUES), _picklist_canonical(INDUSTRY_VALUES)),
    "account_owner": FieldRule(SalesforceObject.ACCOUNT, "OwnerId", check_user_id, _user_id_canonical),
    "amount": FieldRule(SalesforceObject.OPPORTUNITY, "Amount", check_amount, _amount_canonical),
    "status": FieldRule(SalesforceObject.LEAD, "Status",
                        check_picklist(LEAD_STATUS_VALUES), _picklist_canonical(LEAD_STATUS_VALUES)),
}

_OBJECT_LOOKUP = {o.value.lower(): o for o in SalesforceObject}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _allowed_fields_text() -> str:
    return "; ".join(f"{name} ({rule.object_type.value})" for name, rule in ALLOWED_FIELDS.items())


# ---------------------------------------------------------------------------
# Template download
# ---------------------------------------------------------------------------


def template_csv() -> str:
    """The template with example rows that pass validation against the mock Salesforce data."""
    acme = ["0015g00000AbCdEAAV", "Acme GmbH", "anna@acme.com", "Buyer"]
    globex = ["0015g00000AbCdFAAV", "Globex AG", "bo@globex.com", "CFO"]
    examples = [
        acme + ["Account", "0015g00000AbCdEAAV", "industry", EMPTY_TOKEN, "Banking"],
        acme + ["Account", "0015g00000AbCdEAAV", "account_owner", "0055g00000XyZaBAAV", "0055g00000QrStUAAV"],
        acme + ["Opportunity", "0065g00000LmNoPAAV", "amount", "12000.00", "15500.00"],
        globex + ["Account", "0015g00000AbCdFAAV", "country", "Germany", "Austria"],
        globex + ["Lead", "00Q5g00000HiJkLEAV", "status", "Open - Not Contacted", "Working - Contacted"],
    ]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(TEMPLATE_COLUMNS)
    writer.writerows(examples)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Parsing + format checks
# ---------------------------------------------------------------------------


@dataclass
class ParsedRow:
    row_number: int
    salesforce_account_id: str   # 18-character
    name: str
    email: str
    role: str
    object_type: SalesforceObject
    record_id: str               # 18-character
    field: str                   # key of ALLOWED_FIELDS
    current_value: str           # "" when the user wrote (empty)
    new_value: str


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


def parse_csv(raw: bytes) -> tuple[list[ParsedRow], list[ValidationIssue], int]:
    """Returns (valid rows, issues, number of data rows in the file).

    The file is only usable when `issues` is empty.
    """
    issues: list[ValidationIssue] = []

    def file_error(code: str, message: str) -> tuple[list[ParsedRow], list[ValidationIssue], int]:
        return [], [ValidationIssue(code=code, message=message)], 0

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
    missing = [c for c in TEMPLATE_COLUMNS if c not in columns]
    unknown = [header[i].strip() for i, c in enumerate(columns) if c and c not in TEMPLATE_COLUMNS]
    dupes = sorted({c for c in columns if c and columns.count(c) > 1})
    if missing:
        issues.append(ValidationIssue(code="missing_column",
                                      message=f"Missing mandatory column(s): {', '.join(missing)}. "
                                              f"The template has: {','.join(TEMPLATE_COLUMNS)}"))
    if unknown:
        issues.append(ValidationIssue(code="unknown_column",
                                      message=f"Column(s) not allowed: {', '.join(unknown)}. "
                                              f"The template has: {','.join(TEMPLATE_COLUMNS)}"))
    if dupes:
        issues.append(ValidationIssue(code="duplicate_column",
                                      message=f"Column(s) appear more than once: {', '.join(dupes)}."))
    if issues:
        return [], issues, 0
    index = {c: columns.index(c) for c in TEMPLATE_COLUMNS}

    # --- rows -------------------------------------------------------------
    rows: list[ParsedRow] = []
    data_rows = 0
    try:
        for line_no, values in enumerate(reader, start=2):
            if not any(v.strip() for v in values):
                continue  # skip blank lines
            data_rows += 1
            if data_rows > MAX_ROWS:
                issues.append(ValidationIssue(code="too_many_rows",
                                              message=f"The file has more than {MAX_ROWS} rows. "
                                                      "Split it into several requests."))
                break
            if len(values) > len(header):
                issues.append(ValidationIssue(row=line_no, code="extra_values",
                                              message=f"Row has {len(values)} values but the header has "
                                                      f"{len(header)} columns. Check for unquoted commas."))
                continue
            row = _check_row(line_no, values, index, issues)
            if row:
                rows.append(row)
    except csv.Error as exc:
        issues.append(ValidationIssue(code="bad_csv", message=f"The CSV is malformed: {exc}"))

    issues += check_duplicates_in_file(rows)
    if data_rows == 0 and not issues:
        issues.append(ValidationIssue(code="no_rows", message="The file has a header but no change rows."))
    return rows, issues, data_rows


def _check_row(line_no: int, values: list[str], index: dict[str, int],
               issues: list[ValidationIssue]) -> Optional[ParsedRow]:
    def get(col: str) -> str:
        i = index[col]
        return values[i].strip() if i < len(values) else ""

    def err(column: str, code: str, message: str) -> None:
        issues.append(ValidationIssue(row=line_no, column=column, code=code, message=message))

    before = len(issues)

    # Every column is mandatory
    for col in TEMPLATE_COLUMNS:
        if not get(col):
            hint = f" Write {EMPTY_TOKEN} if the field is blank in Salesforce." if col == "current_value" else ""
            err(col, "missing_value", f"'{col}' is mandatory.{hint}")

    # Account
    account_id = None
    if get("account_id"):
        account_id = normalise_sf_id(get("account_id"))
        if account_id is None:
            err("account_id", "invalid_id", f"'{get('account_id')}' is not a valid Salesforce ID. "
                "Copy the 15- or 18-character ID from the account's page in Salesforce.")
        elif not account_id.startswith(ID_PREFIX[SalesforceObject.ACCOUNT]):
            err("account_id", "wrong_id_type", f"'{get('account_id')}' is not an Account ID "
                f"(Account IDs start with {ID_PREFIX[SalesforceObject.ACCOUNT]}).")
    if get("email") and not _EMAIL_RE.match(get("email")):
        err("email", "invalid_value", f"'{get('email')}' is not a valid email address.")
    for col, max_length in (("name", 255), ("role", 100)):
        if len(get(col)) > max_length:
            err(col, "invalid_value", f"Value is longer than {max_length} characters.")

    # Object + field must be an allowed change
    obj = _OBJECT_LOOKUP.get(get("object").lower()) if get("object") else None
    if get("object") and obj is None:
        err("object", "invalid_object",
            f"'{get('object')}' can't be changed. Allowed: {', '.join(o.value for o in SalesforceObject)}.")

    field = get("field").lower() if get("field") else None
    rule = ALLOWED_FIELDS.get(field) if field else None
    if field and rule is None:
        err("field", "field_not_allowed",
            f"'{get('field')}' can't be changed. Allowed fields: {_allowed_fields_text()}.")
    elif rule and obj and rule.object_type != obj:
        err("field", "field_wrong_object",
            f"'{field}' is a field of {rule.object_type.value}, not {obj.value}.")
        rule = None

    # Record ID: valid, the right object type, and for Account changes the account itself
    record_id = None
    if get("record_id"):
        record_id = normalise_sf_id(get("record_id"))
        if record_id is None:
            err("record_id", "invalid_id", f"'{get('record_id')}' is not a valid Salesforce ID. "
                "Copy the 15- or 18-character ID from the record's page in Salesforce.")
        elif obj and not record_id.startswith(ID_PREFIX[obj]):
            err("record_id", "wrong_id_type", f"'{get('record_id')}' is not an ID of a {obj.value} "
                f"({obj.value} IDs start with {ID_PREFIX[obj]}).")
        elif obj == SalesforceObject.ACCOUNT and account_id and not same_sf_id(record_id, account_id):
            err("record_id", "account_mismatch",
                "For Account changes, record_id must be the same as account_id.")

    # Values follow the field's rules
    current_raw, new_raw = get("current_value"), get("new_value")
    current = "" if current_raw.lower() == EMPTY_TOKEN else current_raw
    if rule:
        if new_raw.lower() == EMPTY_TOKEN:
            err("new_value", "empty_new_value", f"'{field}' can't be cleared through this process.")
        elif new_raw and (msg := rule.check(new_raw)):
            err("new_value", "invalid_value", msg)
        if current and (msg := rule.check(current)):
            err("current_value", "invalid_value", msg)
        if current_raw and new_raw and len(issues) == before and rule.same(current, new_raw):
            err("new_value", "no_change", "new_value is the same as current_value.")

    if len(issues) > before:
        return None
    assert account_id and obj and record_id and field and rule
    return ParsedRow(
        row_number=line_no,
        salesforce_account_id=account_id,
        name=get("name"),
        email=get("email"),
        role=get("role"),
        object_type=obj,
        record_id=record_id,
        field=field,
        current_value=rule.canonical(current) if current else "",
        new_value=rule.canonical(new_raw),
    )


def check_duplicates_in_file(rows: list[ParsedRow]) -> list[ValidationIssue]:
    """The same record + field may appear only once in a file."""
    first_seen: dict[tuple[str, str], int] = {}
    issues = []
    for row in rows:
        key = (row.record_id.upper(), row.field)
        if key in first_seen:
            issues.append(ValidationIssue(
                row=row.row_number, column="record_id", code="duplicate_in_file",
                message=f"{row.object_type.value} {row.record_id} '{row.field}' is already changed on "
                        f"line {first_seen[key]}. Each record and field may appear only once.",
            ))
        else:
            first_seen[key] = row.row_number
    return issues


def summarise(rows: list[ParsedRow]) -> str:
    """Short description for change_requests.requested_change, e.g.
    '5 changes on 2 accounts: amount (1), country (1), industry (2), status (1)'."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.field] = counts.get(row.field, 0) + 1
    accounts = len({row.salesforce_account_id for row in rows})
    parts = ", ".join(f"{f} ({n})" for f, n in sorted(counts.items()))
    return (f"{len(rows)} change{'s' if len(rows) != 1 else ''} on {accounts} "
            f"account{'s' if accounts != 1 else ''}: {parts}")