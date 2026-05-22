#!/usr/bin/env python3
"""Create Google Sheets batchUpdate payloads for the permanent result sheet."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SPREADSHEET_ID = "151fl2XsI_gmqPXIhFA47OZ-nSQEMBbDb2JaKNXN9be0"
SHEET_ID = 1822087262
SHEET_TITLE = "Номенклатура"
FIELDS = [
    "Конкурент",
    "Категория",
    "Наименование",
    "Цена",
    "Цена со скидкой",
    "Ссылка",
    "Якорь",
]


def cell(value: str) -> dict[str, object]:
    value = "" if value is None else str(value)
    if value.isdigit():
        return {"userEnteredValue": {"numberValue": int(value)}}
    return {"userEnteredValue": {"stringValue": value}}


def row(values: list[str]) -> dict[str, object]:
    return {"values": [cell(value) for value in values]}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def staging_sheet_id() -> int:
    return int(datetime.now(ZoneInfo("Europe/Moscow")).strftime("8%H%M%S"))


def build_payloads(
    rows: list[dict[str, str]],
    updated_at: str,
    chunk_size: int,
    staging_id: int,
) -> list[dict[str, object]]:
    max_rows = max(len(rows) + 1, 2)
    payloads: list[dict[str, object]] = [
        {
            "spreadsheetId": SPREADSHEET_ID,
            "requests": [
                {
                    "addSheet": {
                        "properties": {
                            "sheetId": staging_id,
                            "title": f"{SHEET_TITLE}_staging_{staging_id}",
                            "gridProperties": {
                                "rowCount": max_rows,
                                "columnCount": 8,
                                "frozenRowCount": 1,
                            },
                        },
                    }
                },
            ],
        }
    ]

    header = FIELDS + [updated_at]
    data_rows = [header]
    data_rows.extend([[item.get(field, "") for field in FIELDS] for item in rows])

    for start in range(0, len(data_rows), chunk_size):
        chunk = data_rows[start : start + chunk_size]
        payloads.append(
            {
                "spreadsheetId": SPREADSHEET_ID,
                "requests": [
                    {
                        "updateCells": {
                            "start": {
                                "sheetId": staging_id,
                                "rowIndex": start,
                                "columnIndex": 0,
                            },
                            "rows": [row(values) for values in chunk],
                            "fields": "userEnteredValue",
                        }
                    }
                ],
            }
        )

    payloads.append(
        {
            "spreadsheetId": SPREADSHEET_ID,
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": SHEET_ID,
                            "title": SHEET_TITLE,
                            "gridProperties": {
                                "rowCount": max_rows,
                                "columnCount": 8,
                                "frozenRowCount": 1,
                            },
                        },
                        "fields": "title,gridProperties(rowCount,columnCount,frozenRowCount)",
                    }
                },
                {
                    "updateCells": {
                        "range": {
                            "sheetId": SHEET_ID,
                            "startRowIndex": 0,
                            "endRowIndex": max_rows,
                            "startColumnIndex": 0,
                            "endColumnIndex": 8,
                        },
                        "fields": "userEnteredValue",
                    }
                },
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": staging_id,
                            "startRowIndex": 0,
                            "endRowIndex": max_rows,
                            "startColumnIndex": 0,
                            "endColumnIndex": 8,
                        },
                        "destination": {
                            "sheetId": SHEET_ID,
                            "startRowIndex": 0,
                            "endRowIndex": max_rows,
                            "startColumnIndex": 0,
                            "endColumnIndex": 8,
                        },
                        "pasteType": "PASTE_VALUES",
                        "pasteOrientation": "NORMAL",
                    }
                },
                {"deleteSheet": {"sheetId": staging_id}},
                {
                    "autoResizeDimensions": {
                        "dimensions": {
                            "sheetId": SHEET_ID,
                            "dimension": "COLUMNS",
                            "startIndex": 0,
                            "endIndex": 8,
                        }
                    }
                }
            ],
        }
    )
    return payloads


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Собрать batchUpdate JSON для постоянной Google Таблицы.")
    parser.add_argument("--input", default="data/direct_competitors_final.csv", help="Финальный CSV.")
    parser.add_argument("--output-dir", default="outputs/google_sheet_batches", help="Папка batch-файлов.")
    parser.add_argument("--chunk-size", type=int, default=750, help="Строк на один batch-файл.")
    parser.add_argument("--updated-at", default="", help="Дата обновления для H1. По умолчанию текущее МСК-время.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    updated_at = args.updated_at or datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M:%S")
    rows = read_rows(Path(args.input))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    staging_id = staging_sheet_id()
    payloads = build_payloads(rows, updated_at, args.chunk_size, staging_id)
    batch_files: list[str] = []
    for index, payload in enumerate(payloads, start=1):
        path = output_dir / f"batch_{index:03d}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        batch_files.append(str(path))

    manifest = {
        "spreadsheetId": SPREADSHEET_ID,
        "sheetId": SHEET_ID,
        "sheetTitle": SHEET_TITLE,
        "stagingSheetId": staging_id,
        "updatedAt": updated_at,
        "rows": len(rows),
        "batchFiles": batch_files,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Готово batch-файлов: {len(batch_files)}")
    print(f"Строк данных: {len(rows)}")
    print(f"H1: {updated_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
