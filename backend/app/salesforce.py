"""
Salesforce access for validation.

Until Salesforce is connected, the app uses `MockSalesforceClient`, which reads
records from a local JSON file (mock_salesforce_data.json). When you connect
Salesforce, add a real client with the same `get_records` method (for example
with the `simple-salesforce` package) and return it from `get_salesforce_client()`.
The validation checks don't need to change.

SALESFORCE_MODE in .env picks the client:
    mock  (default) - read SALESFORCE_MOCK_FILE (default: mock_salesforce_data.json)
    off             - skip the Salesforce checks (a warning is shown instead)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Protocol

from schemas import SalesforceObject

DEFAULT_MOCK_FILE = Path(__file__).with_name("mock_salesforce_data.json")


class SalesforceUnavailable(Exception):
    """Salesforce (or the mock file) can't be reached; validation can't be completed."""


class SalesforceClient(Protocol):
    def get_records(
        self, object_type: SalesforceObject, record_ids: list[str], fields: list[str]
    ) -> dict[str, dict[str, Optional[str]]]:
        """Return {18-character record id: {field API name: value as text or None}}
        for the IDs that exist. IDs that don't exist are missing from the result.

        A real client would run one SOQL query per object, e.g.
            SELECT Id, Industry, OwnerId FROM Account WHERE Id IN ('001...', '001...')
        """
        ...


class MockSalesforceClient:
    """Reads records from a JSON file shaped like:

    {
      "Account":     {"0015g00000AbCdEAAV": {"Industry": null, "OwnerId": "0055g00000XyZaBAAV"}},
      "Opportunity": {"0065g00000LmNoPAAV": {"AccountId": "0015g00000AbCdEAAV", "Amount": 12000}},
      "Lead":        {"00Q5g00000HiJkLEAV": {"Status": "Open - Not Contacted"}}
    }
    """

    def __init__(self, data: dict[str, dict[str, dict[str, object]]]):
        # 18-character IDs are case-insensitive, so index by upper case.
        self._data = {
            obj: {rid.upper(): fields for rid, fields in records.items()}
            for obj, records in data.items()
        }

    @classmethod
    def from_file(cls, path: Path) -> "MockSalesforceClient":
        try:
            return cls(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            raise SalesforceUnavailable(f"The mock Salesforce file {path} doesn't exist.")
        except json.JSONDecodeError as exc:
            raise SalesforceUnavailable(f"The mock Salesforce file {path} is not valid JSON: {exc}")

    def get_records(self, object_type, record_ids, fields):
        records = self._data.get(object_type.value, {})
        result = {}
        for rid in record_ids:
            found = records.get(rid.upper())
            if found is not None:
                result[rid] = {f: None if found.get(f) is None else str(found[f]) for f in fields}
        return result


def get_salesforce_client() -> Optional[SalesforceClient]:
    """The client to validate against, or None when the Salesforce checks are switched off.

    Raises SalesforceUnavailable when the checks are on but can't be run.
    """
    mode = os.getenv("SALESFORCE_MODE", "mock").lower()
    if mode == "off":
        return None
    if mode == "mock":
        return MockSalesforceClient.from_file(Path(os.getenv("SALESFORCE_MOCK_FILE", DEFAULT_MOCK_FILE)))
    # TODO(salesforce): mode == "live" -> return your real client here.
    raise SalesforceUnavailable(f"Unknown SALESFORCE_MODE '{mode}' (use mock or off).")