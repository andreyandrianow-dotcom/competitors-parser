#!/usr/bin/env python3
"""Merge open-site rows with browser-priced DNS/MTS rows into the final table."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, OrderedDict
from pathlib import Path

from validate_competitors_result import (
    ALLOWED_CATEGORIES,
    ALLOWED_COMPETITORS,
    REQUIRED_COLUMNS,
    accessory_marker,
    valid_price,
)


OPEN_SITE_COMPETITORS = {"Билайн", "Мегафон"}
BROWSER_PRICED_COMPETITORS = {"ДНС", "МТС"}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def clean_row(row: dict[str, str]) -> dict[str, str]:
    return {column: (row.get(column) or "").strip() for column in REQUIRED_COLUMNS}


def is_final_row(row: dict[str, str]) -> bool:
    if row["Конкурент"] not in ALLOWED_COMPETITORS:
        return False
    if row["Категория"] not in ALLOWED_CATEGORIES:
        return False
    if not row["Наименование"] or not row["Ссылка"] or not row["Якорь"]:
        return False
    if not row["Ссылка"].startswith("https://"):
        return False
    if not valid_price(row["Цена"]):
        return False
    if row["Цена со скидкой"] and not valid_price(row["Цена со скидкой"]):
        return False
    return not accessory_marker(row)


def merge_rows(base_rows: list[dict[str, str]], priced_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    by_key: OrderedDict[tuple[str, str], dict[str, str]] = OrderedDict()

    source_rows = [
        clean_row(row)
        for row in base_rows
        if (row.get("Конкурент") or "").strip() in OPEN_SITE_COMPETITORS
    ]
    source_rows.extend(
        clean_row(row)
        for row in priced_rows
        if (row.get("Конкурент") or "").strip() in BROWSER_PRICED_COMPETITORS
    )

    for row in source_rows:
        if not is_final_row(row):
            continue
        key = (row["Конкурент"], row["Ссылка"])
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = row
            continue
        if not existing["Цена со скидкой"] and row["Цена со скидкой"]:
            by_key[key] = row

    return sorted(
        by_key.values(),
        key=lambda row: (row["Конкурент"], row["Категория"], row["Наименование"], row["Ссылка"]),
    )


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_json(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Собрать финальный файл с ценами DNS и MTS.")
    parser.add_argument("--base", default="data/direct_competitors_final.csv", help="Файл с Билайн/Мегафон.")
    parser.add_argument("--priced", default="data/browser_dns_mts_prices.csv", help="Файл DNS/MTS с ценами.")
    parser.add_argument("--output", default="data/direct_competitors_final.csv", help="Итоговый CSV.")
    parser.add_argument("--json-output", default="data/direct_competitors_final.json", help="Итоговый JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = merge_rows(read_rows(Path(args.base)), read_rows(Path(args.priced)))
    write_csv(rows, Path(args.output))
    if args.json_output:
        write_json(rows, Path(args.json_output))

    counts = Counter(row["Конкурент"] for row in rows)
    missing_prices = sum(1 for row in rows if not row["Цена"])
    print(f"Готово: {args.output} ({len(rows)} строк)")
    print(f"Конкуренты: {dict(counts)}")
    print(f"Строк без цены: {missing_prices}")
    if args.json_output:
        print(f"JSON: {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
