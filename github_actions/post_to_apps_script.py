#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


def moscow_now() -> str:
    return datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M:%S")


def row_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/direct_competitors_final.csv")
    parser.add_argument("--validation", default="data/direct_competitors_validation.json")
    args = parser.parse_args()

    url = os.environ["APPS_SCRIPT_WEBAPP_URL"]
    token = os.environ["APPS_SCRIPT_UPLOAD_TOKEN"]

    csv_path = Path(args.input)
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    validation_path = Path(args.validation)
    validation = json.loads(validation_path.read_text(encoding="utf-8")) if validation_path.exists() else {}

    payload = {
        "token": token,
        "updatedAt": moscow_now(),
        "rows": row_count(csv_path),
        "validation": validation,
        "csvGzipBase64": base64.b64encode(gzip.compress(csv_text.encode("utf-8"))).decode("ascii"),
    }
    response = requests.post(url, json=payload, timeout=300)
    print(response.status_code, response.text[:1000])
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
