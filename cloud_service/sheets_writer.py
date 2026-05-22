"""Google Sheets writer for the permanent competitor sheet."""

from __future__ import annotations

import json
import os
from pathlib import Path

import google.auth
from google.oauth2 import service_account
from googleapiclient.discovery import build


SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "151fl2XsI_gmqPXIhFA47OZ-nSQEMBbDb2JaKNXN9be0")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def credentials():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if path:
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    creds, _ = google.auth.default(scopes=SCOPES)
    return creds


def service():
    return build("sheets", "v4", credentials=credentials(), cache_discovery=False)


def batch_update(requests: list[dict[str, object]], spreadsheet_id: str = SPREADSHEET_ID) -> None:
    service().spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


def apply_manifest(manifest_path: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for batch_file in manifest["batchFiles"]:
        payload = json.loads(Path(batch_file).read_text(encoding="utf-8"))
        batch_update(payload["requests"], payload.get("spreadsheetId") or manifest["spreadsheetId"])
    return manifest


def read_control_values(spreadsheet_id: str = SPREADSHEET_ID) -> dict[str, str]:
    result = (
        service()
        .spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range="'ParserControl'!A1:B100")
        .execute()
    )
    rows = result.get("values", [])
    values: dict[str, str] = {}
    for row in rows[1:]:
        if row:
            values[str(row[0])] = str(row[1]) if len(row) > 1 else ""
    return values


def set_control_values(values: dict[str, str], spreadsheet_id: str = SPREADSHEET_ID) -> None:
    merged = read_control_values(spreadsheet_id)
    merged.update({key: str(value) for key, value in values.items()})
    requests = []
    rows = [["key", "value"]] + [[key, value] for key, value in sorted(merged.items())]
    requests.append(
        {
            "updateCells": {
                "range": {
                    "sheetId": 906021521,
                    "startRowIndex": 0,
                    "endRowIndex": max(20, len(rows)),
                    "startColumnIndex": 0,
                    "endColumnIndex": 2,
                },
                "fields": "userEnteredValue",
            }
        }
    )
    requests.append(
        {
            "updateCells": {
                "start": {"sheetId": 906021521, "rowIndex": 0, "columnIndex": 0},
                "rows": [
                    {
                        "values": [
                            {"userEnteredValue": {"stringValue": str(row[0])}},
                            {"userEnteredValue": {"stringValue": str(row[1])}},
                        ]
                    }
                    for row in rows
                ],
                "fields": "userEnteredValue",
            }
        }
    )
    batch_update(requests, spreadsheet_id)
