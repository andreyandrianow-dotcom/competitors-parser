#!/usr/bin/env python3
"""Build the user-facing competitor result table.

The direct parser keeps technical columns for diagnostics. This script turns
the raw parser output into the final business table requested by the user.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse


FINAL_FIELDS = (
    "Конкурент",
    "Категория",
    "Наименование",
    "Цена",
    "Цена со скидкой",
    "Ссылка",
    "Якорь",
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


@dataclass
class FinalRow:
    Конкурент: str
    Категория: str
    Наименование: str
    Цена: str
    Цена_со_скидкой: str
    Ссылка: str
    Якорь: str

    def as_csv_dict(self) -> dict[str, str]:
        return {
            "Конкурент": self.Конкурент,
            "Категория": self.Категория,
            "Наименование": self.Наименование,
            "Цена": self.Цена,
            "Цена со скидкой": self.Цена_со_скидкой,
            "Ссылка": self.Ссылка,
            "Якорь": self.Якорь,
        }


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("http://moskva.beeline.ru/"):
        url = "https://" + url[len("http://") :]
    return url


def normalize_price(value: str) -> str:
    value = (value or "").replace("\xa0", " ").strip()
    value = re.sub(r"[^\d,.\-]", "", value).replace(",", ".")
    if value.endswith(".0"):
        value = value[:-2]
    return value


def is_accessory(name: str, url: str) -> bool:
    text = f"{name} {url}".lower()
    markers = (
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
        "batarea",
        "display",
        "displej",
        "ekran",
        "modul",
        "shlejf",
        "razem",
        "korpus",
        "steklo",
        "sensor",
        "dinamik",
        "kamera",
        "zapcast",
        "zapchast",
    )
    marker_found = any(marker in text for marker in markers)
    if not marker_found:
        return False
    if ACCESSORY_LEAD_RE.search(name):
        return True
    return not DEVICE_PRODUCT_RE.search(name)


def normalize_category(row: dict[str, str]) -> str:
    name = row.get("name", "")
    url = normalize_url(row.get("product_url", ""))
    raw_category = row.get("category", "")
    device_type = row.get("device_type", "")
    text = f"{raw_category} {device_type} {name} {url}".lower()

    if any(marker in text for marker in ("планш", "planshet", "planset", "tablet", "ipad")):
        return "Планшет"
    if any(
        marker in text
        for marker in (
            "кнопоч",
            "мобильный телефон",
            "mobilnyj-telefon",
            "mobilnyy-telefon",
            "mobilnyi-telefon",
            "sotovyj-telefon",
            "feature_phone",
        )
    ):
        return "Телефон"
    if any(marker in text for marker in ("смартф", "smartfon", "smartphone", "iphone")):
        return "Смартфон"
    return ""


def extract_anchor(row: dict[str, str]) -> str:
    competitor = row.get("competitor") or row.get("competitor_name") or ""
    url = normalize_url(row.get("product_url", ""))
    product_id = (row.get("product_id") or row.get("item_code") or "").strip()

    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not product_id or product_id in {"courier", "delivery"}:
        parts = path.split("/")
        if "details" in parts:
            idx = parts.index("details")
            product_id = parts[idx + 1] if len(parts) > idx + 1 else ""
        elif "product" in parts:
            idx = parts.index("product")
            product_id = parts[idx + 1] if len(parts) > idx + 1 else ""
        else:
            product_id = parts[-1] if parts else ""

    return f"{competitor}:{product_id or path}"


def is_valid_product_row(row: dict[str, str]) -> bool:
    url = normalize_url(row.get("product_url", ""))
    name = (row.get("name") or "").strip()
    competitor = row.get("competitor_name", "")

    if not url or not name:
        return False
    if competitor in {"Билайн", "Мегафон"} and not normalize_price(row.get("retail_price", "")):
        return False
    if is_accessory(name, url):
        return False
    if competitor == "Билайн" and not re.search(r"^https://moskva\.beeline\.ru/shop/details/[^/]+/?$", url):
        return False
    if normalize_category(row) not in {"Смартфон", "Планшет", "Телефон"}:
        return False
    return True


def to_final_row(row: dict[str, str]) -> FinalRow:
    price = normalize_price(row.get("retail_price", ""))
    discount_price = normalize_price(row.get("discount_price", "") or row.get("sale_price", ""))
    return FinalRow(
        Конкурент=row.get("competitor_name", "") or row.get("competitor", ""),
        Категория=normalize_category(row),
        Наименование=(row.get("name") or "").strip(),
        Цена=price,
        Цена_со_скидкой=discount_price,
        Ссылка=normalize_url(row.get("product_url", "")),
        Якорь=extract_anchor(row),
    )


def build_final_rows(raw_paths: list[Path]) -> list[FinalRow]:
    by_key: OrderedDict[tuple[str, str], FinalRow] = OrderedDict()
    source_rows: list[dict[str, str]] = []
    for path in raw_paths:
        source_rows.extend(read_rows(path))

    for row in source_rows:
        if not is_valid_product_row(row):
            continue
        final = to_final_row(row)
        key = (final.Конкурент, final.Ссылка)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = final
            continue
        if (not existing.Цена and final.Цена) or (existing.Якорь.endswith(":") and final.Якорь):
            by_key[key] = final

    return sorted(by_key.values(), key=lambda item: (item.Конкурент, item.Категория, item.Наименование, item.Ссылка))


def write_csv(rows: list[FinalRow], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FINAL_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv_dict())


def write_json(rows: list[FinalRow], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps([row.as_csv_dict() for row in rows], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Собрать финальную таблицу конкурентов в нужных колонках.")
    parser.add_argument(
        "--raw",
        action="append",
        default=[],
        help="Raw CSV парсера. Можно указать несколько раз.",
    )
    parser.add_argument("--output", default="data/direct_competitors_final.csv", help="Финальный CSV.")
    parser.add_argument("--json-output", default="data/direct_competitors_final.json", help="Финальный JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw_paths = [Path(path) for path in args.raw] or [
        Path("data/direct_competitors.csv"),
        Path("data/direct_competitors_open_sites_fixed.csv"),
    ]
    rows = build_final_rows(raw_paths)
    write_csv(rows, Path(args.output))
    if args.json_output:
        write_json(rows, Path(args.json_output))
    print(f"Готово: {args.output} ({len(rows)} строк)")
    if args.json_output:
        print(f"JSON: {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
