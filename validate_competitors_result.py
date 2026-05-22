#!/usr/bin/env python3
"""Validate the final competitor result against the requested structure."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


REQUIRED_COLUMNS = [
    "Конкурент",
    "Категория",
    "Наименование",
    "Цена",
    "Цена со скидкой",
    "Ссылка",
    "Якорь",
]
ALLOWED_COMPETITORS = {"Билайн", "МТС", "ДНС", "Мегафон"}
ALLOWED_CATEGORIES = {"Смартфон", "Планшет", "Телефон"}
PRICE_REQUIRED_COMPETITORS = ALLOWED_COMPETITORS
ACCESSORY_MARKERS = (
    "чехол",
    "накладка",
    "стекло",
    "пленк",
    "кабель",
    "заряд",
    "адаптер",
    "держатель",
    "наушник",
    "колонка",
    "сим-карта",
    "тариф",
    "стилус",
    "stylus",
    "pencil",
    "монитор",
    "ноутбук",
    "графическ",
    "graficesk",
    "graphic",
    "antenna",
    "antena",
    "akkumulator",
    "batareya",
    "display",
    "displej",
    "ekran",
    "modul",
    "shlejf",
    "razem",
    "korpus",
    "sensor",
    "zapcast",
    "zapchast",
)
ACCESSORY_LEAD_RE = re.compile(
    r"^\s*(?:чехол|защитн|стекло|пленк|кабель|заряд|адаптер|держатель|"
    r"наушник|колонка|сим-карта|тариф|стилус|stylus|pencil)\b",
    re.IGNORECASE,
)
DEVICE_PRODUCT_RE = re.compile(
    r"\b(?:смартфон|планшет|мобильный телефон|кнопочный телефон|телефон)\b",
    re.IGNORECASE,
)


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def valid_price(value: str) -> bool:
    if not value:
        return False
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", value.strip()))


def sluggy_name(row: dict[str, str]) -> bool:
    name = row["Наименование"].strip()
    if row["Конкурент"] not in {"ДНС", "МТС"}:
        return False
    if re.fullmatch(r"[a-z0-9/.,+() -]+", name.lower()):
        return True
    return bool(re.search(r"\b(?:gb|tb|wi fi|chernyj|belyj|seryi|sinii|krasnyj|zelenyj)\b", name.lower()))


def accessory_marker(row: dict[str, str]) -> str:
    name = row.get("Наименование", "")
    text = f"{name} {row.get('Ссылка', '')}".lower()
    marker = next((marker for marker in ACCESSORY_MARKERS if marker in text), "")
    if not marker:
        return ""
    if ACCESSORY_LEAD_RE.search(name):
        return marker
    if DEVICE_PRODUCT_RE.search(name):
        return ""
    return marker


def validate(path: Path) -> dict[str, object]:
    headers, rows = read_rows(path)
    errors: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    seen_links: set[tuple[str, str]] = set()
    counters = {
        "rows": len(rows),
        "competitors": Counter(row.get("Конкурент", "") for row in rows),
        "categories": Counter(row.get("Категория", "") for row in rows),
        "with_price": Counter(row.get("Конкурент", "") for row in rows if row.get("Цена", "").strip()),
        "empty_price": Counter(row.get("Конкурент", "") for row in rows if not row.get("Цена", "").strip()),
    }

    if headers != REQUIRED_COLUMNS:
        errors.append({"type": "columns", "expected": REQUIRED_COLUMNS, "actual": headers})

    for index, row in enumerate(rows, start=2):
        for column in ("Конкурент", "Категория", "Наименование", "Ссылка", "Якорь"):
            if not row.get(column, "").strip():
                errors.append({"type": "empty_required", "row": index, "column": column})

        if row.get("Конкурент") not in ALLOWED_COMPETITORS:
            errors.append({"type": "bad_competitor", "row": index, "value": row.get("Конкурент")})
        if row.get("Категория") not in ALLOWED_CATEGORIES:
            errors.append({"type": "bad_category", "row": index, "value": row.get("Категория")})

        url = row.get("Ссылка", "")
        if not url.startswith("https://"):
            errors.append({"type": "bad_url_scheme", "row": index, "value": url})
        if "/delivery/" in url or row.get("Наименование") == "courier":
            errors.append({"type": "service_url", "row": index, "url": url})

        key = (row.get("Конкурент", ""), url)
        if key in seen_links:
            errors.append({"type": "duplicate_link", "row": index, "key": key})
        seen_links.add(key)

        marker = accessory_marker(row)
        if marker:
            errors.append({"type": "accessory_marker", "row": index, "marker": marker, "name": row.get("Наименование"), "url": url})

        if row.get("Цена") and not valid_price(row["Цена"]):
            errors.append({"type": "bad_price", "row": index, "value": row["Цена"]})
        if row.get("Цена со скидкой") and not valid_price(row["Цена со скидкой"]):
            errors.append({"type": "bad_discount_price", "row": index, "value": row["Цена со скидкой"]})
        if row.get("Конкурент") in PRICE_REQUIRED_COMPETITORS and not row.get("Цена"):
            errors.append({"type": "missing_price", "row": index, "competitor": row.get("Конкурент"), "url": url})

        if sluggy_name(row):
            warnings.append({"type": "name_from_slug", "row": index, "competitor": row.get("Конкурент"), "name": row.get("Наименование"), "url": url})

    return {
        "ok": not errors,
        "errors_count": len(errors),
        "warnings_count": len(warnings),
        "counters": {
            "rows": counters["rows"],
            "competitors": dict(counters["competitors"]),
            "categories": dict(counters["categories"]),
            "with_price": dict(counters["with_price"]),
            "empty_price": dict(counters["empty_price"]),
        },
        "errors": errors[:200],
        "warnings": warnings[:200],
        "notes": [
            "Цена обязательна для всех конкурентов. Карточки без текущей цены не проходят в финальный результат.",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Проверить финальную таблицу конкурентов.")
    parser.add_argument("--input", default="data/direct_competitors_final.csv", help="Финальный CSV.")
    parser.add_argument("--report", default="data/direct_competitors_validation.json", help="JSON-отчет проверки.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = validate(Path(args.input))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Проверено: {args.input}")
    print(f"Ошибок: {report['errors_count']}; предупреждений: {report['warnings_count']}")
    print(f"Отчет: {args.report}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
