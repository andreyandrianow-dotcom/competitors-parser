#!/usr/bin/env python3
"""Parse Zoomos B2B nomenclature for phones and tablets.

The script logs in through my.zoomos.by, opens the B2B catalog, initializes
each category filter, and then reads the paginated table component until all
rows are collected.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import html
import json
import math
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from http.cookiejar import CookieJar


DEFAULT_CATEGORIES = ("Мобильные телефоны", "Планшеты")
DEFAULT_CURRENCY = "RUB"
LOGIN_URL = "https://my.zoomos.by/login"
B2B_PRICELIST_URL = "https://b2b.zoomos.by/pricelist"
B2B_COMPONENT_URL = "https://b2b.zoomos.by/components/pricelist_page"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)


@dataclass
class ProductRow:
    scraped_at: str
    category: str
    device_type: str
    product_id: str
    spra_id: str
    name: str
    supplier: str
    supplier_item_id: str
    wholesale_price: str
    retail_price: str
    item_code: str
    updated_at: str
    product_url: str
    supplier_url: str
    source_page: int


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def clean_text(value: str) -> str:
    value = re.sub(r"<!--.*?-->", " ", value, flags=re.S)
    value = re.sub(r"<script\b.*?</script>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<style\b.*?</style>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def strip_tags_keep_comments(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def find_first(pattern: str, text: str, default: str = "", flags: int = re.S | re.I) -> str:
    match = re.search(pattern, text, flags)
    return html.unescape(match.group(1)).strip() if match else default


def extract_rows(table_html: str, category_hint: str, source_page: int, scraped_at: str) -> list[ProductRow]:
    rows: list[ProductRow] = []
    row_blocks = re.findall(r"<tr\b[^>]*>(.*?)</tr>", table_html, flags=re.S | re.I)

    for block in row_blocks:
        if "/product/" not in block:
            continue

        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", block, flags=re.S | re.I)
        if len(cells) < 7:
            continue

        product_url = find_first(r"<a[^>]+href=['\"]([^'\"]*/product/[^'\"]+)['\"]", block)
        product_id = find_first(r"/product/(\d+)", product_url)
        product_name = clean_text(cells[2])
        product_name = re.sub(r"\s*под заказ\s*$", "", product_name, flags=re.I).strip()

        supplier_url = find_first(r"<a[^>]+href=['\"]([^'\"]*/supplier/[^'\"]+)['\"]", block)
        supplier = clean_text(cells[3])
        supplier = re.sub(r"\s*(опт|розн)\s*$", "", supplier, flags=re.I).strip()
        supplier_item_id = ""
        if supplier_url:
            supplier_item_id = parse_qs(urlparse(supplier_url).query).get("itemId", [""])[0]

        category = clean_text(cells[1]) or category_hint
        if not is_relevant_product(category, product_name):
            continue

        spra_id = find_first(r"showDescriptionTooltip\(this,\s*(\d+)", block)
        item_code = clean_text(cells[6]) if len(cells) > 6 else ""
        updated_at = clean_text(cells[7]) if len(cells) > 7 else ""

        rows.append(
            ProductRow(
                scraped_at=scraped_at,
                category=category,
                device_type=classify_device(category, product_name),
                product_id=product_id,
                spra_id=spra_id,
                name=product_name,
                supplier=supplier,
                supplier_item_id=supplier_item_id,
                wholesale_price=extract_price(cells[4]) if len(cells) > 4 else "",
                retail_price=extract_price(cells[5]) if len(cells) > 5 else "",
                item_code=item_code,
                updated_at=updated_at,
                product_url=urljoin(B2B_PRICELIST_URL, product_url),
                supplier_url=urljoin(B2B_PRICELIST_URL, supplier_url),
                source_page=source_page,
            )
        )

    return rows


def extract_price(cell_html: str) -> str:
    price_val = clean_text(find_first(r"<span[^>]+class=['\"][^'\"]*price-val[^'\"]*['\"][^>]*>(.*?)</span>", cell_html))
    if price_val:
        return price_val

    title = find_first(r"<img[^>]+class=['\"][^'\"]*cur-icon[^'\"]*['\"][^>]+title=['\"]([^'\"]+)['\"]", cell_html)
    if title:
        return title

    cleaned = clean_text(cell_html)
    if cleaned:
        return cleaned

    commented = strip_tags_keep_comments(cell_html)
    commented = re.sub(r"<!--|-->", " ", commented)
    commented = re.sub(r"\s+", " ", commented).strip()
    return commented


def classify_device(category: str, name: str) -> str:
    category_l = category.lower()
    name_l = name.lower()

    if "планш" in category_l:
        return "tablet"

    feature_markers = (
        "кнопоч",
        "расклад",
        "раскладушка",
        "бабушкофон",
        "feature phone",
        "мобильный телефон",
    )
    smartphone_markers = (
        "смартфон",
        "iphone",
        "android",
        "galaxy",
        "redmi",
        "poco",
        "xiaomi",
        "honor",
        "realme",
        "tecno",
        "infinix",
        "oppo",
        "vivo",
        "oneplus",
        "huawei",
        "pixel",
        "nothing phone",
        "xperia",
        "meizu",
        "nubia",
        "iqoo",
        "5g",
        "4g",
        "lte",
        "wifi",
        "wi-fi",
        "gb/",
        "гб/",
        "gb,",
        "гб,",
    )

    if any(marker in name_l for marker in smartphone_markers):
        return "smartphone"
    if any(marker in name_l for marker in feature_markers):
        return "feature_phone"
    if "мобильные телефоны" in category_l:
        return "feature_phone"
    return "mobile_phone"


def is_relevant_product(category: str, name: str) -> bool:
    category_l = category.lower()
    name_l = name.lower()

    accessory_markers = (
        "чехол",
        "защитное стекло",
        "стекло для",
        "пленка для",
        "плёнка для",
        "фотобумага",
        "зарядное устройство",
        "кабель для",
        "дата-кабель",
        "аккумулятор для",
    )
    if any(marker in name_l for marker in accessory_markers):
        return False

    if "планш" in category_l:
        tablet_markers = (
            "планш",
            "ipad",
            "galaxy tab",
            " tab",
            "tab ",
            "tab-",
            "pad",
            "pmt",
            "teclast",
            "lenovo tb",
            "lenovo yt",
            "lenovo pb",
        )
        return any(marker in name_l for marker in tablet_markers)

    return True


class ZoomosClient:
    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))

    def get(self, url: str, params: dict[str, str] | None = None) -> str:
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with self.opener.open(request, timeout=90) as response:
            return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")

    def post(self, url: str, data: dict[str, str]) -> str:
        payload = urlencode(data).encode("utf-8")
        request = Request(
            url,
            data=payload,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with self.opener.open(request, timeout=90) as response:
            return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")

    def login(self) -> None:
        login_html = self.get(LOGIN_URL)
        action = find_first(r"<form[^>]+id=['\"]loginform['\"][^>]+action=['\"]([^'\"]+)['\"]", login_html)
        if not action:
            raise RuntimeError("Не удалось найти форму входа Zoomos.")

        upd = find_first(r"<input[^>]+name=['\"]upd['\"][^>]+value=['\"]([^'\"]*)['\"]", login_html)
        payload = {
            "referer": "",
            "j_username": self.username,
            "j_password": self.password,
        }
        if upd:
            payload["upd"] = upd

        response = self.post(urljoin(LOGIN_URL, action), payload)
        if "loginfailed" in response.lower() or "name='j_username'" in response:
            raise RuntimeError("Zoomos не принял логин или пароль.")

    def init_category(self, category: str, currency: str) -> tuple[str, int, int, int, int, list[ProductRow]]:
        scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        html_text = self.get(B2B_PRICELIST_URL, {"c": category, "currency": currency})
        found = parse_found_count(html_text)
        page_size = parse_page_size(html_text) or 100
        total_pages = max(1, math.ceil(found / page_size)) if found else 1
        pager_id = parse_pager_id(html_text, currency)
        shop_id = parse_shop_id(html_text)
        rows = extract_rows(html_text, category, 1, scraped_at)
        return pager_id, shop_id, page_size, total_pages, found, rows

    def fetch_component_page(
        self,
        pager_id: str,
        shop_id: int,
        currency: str,
        page_index: int,
        category: str,
    ) -> list[ProductRow]:
        scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        html_text = self.get(
            B2B_COMPONENT_URL,
            {
                "pagerId": pager_id,
                "page": str(page_index),
                "search": "",
                "tmpl": "v2",
                "currency": currency,
                "shopId": str(shop_id),
            },
        )
        return extract_rows(html_text, category, page_index + 1, scraped_at)


def parse_found_count(html_text: str) -> int:
    text = clean_text(html_text)
    match = re.search(r"Найдено:\s*([0-9\s]+)", text)
    if not match:
        return 0
    return int(re.sub(r"\D", "", match.group(1)) or "0")


def parse_page_size(html_text: str) -> int:
    match = re.search(r"pagination_posts.*?<input[^>]+value=['\"]([0-9]+)['\"]", html_text, flags=re.S | re.I)
    return int(match.group(1)) if match else 0


def parse_pager_id(html_text: str, currency: str) -> str:
    pager_id = find_first(r"loadPage\('/components/pricelist_page',\s*'([^']+)'", html_text)
    return pager_id or f"/b2b/pricelist-currency={currency}"


def parse_shop_id(html_text: str) -> int:
    shop_id = find_first(r"shopId=(\d+)", html_text)
    return int(shop_id) if shop_id else 543


def unique_rows(rows: Iterable[ProductRow]) -> list[ProductRow]:
    result: list[ProductRow] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for row in rows:
        key = (
            row.category,
            row.product_id,
            row.supplier,
            row.item_code,
            row.wholesale_price,
            row.retail_price,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def write_csv(rows: list[ProductRow], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(rows[0]).keys()) if rows else [field.name for field in ProductRow.__dataclass_fields__.values()]
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_json(rows: list[ProductRow], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parser = argparse.ArgumentParser(description="Парсер номенклатуры Zoomos B2B: смартфоны, планшеты, кнопочные телефоны.")
    parser.add_argument("--username", default=os.getenv("ZOOMOS_USERNAME"), help="Логин Zoomos. Можно задать через ZOOMOS_USERNAME.")
    parser.add_argument("--password", default=os.getenv("ZOOMOS_PASSWORD"), help="Пароль Zoomos. Можно задать через ZOOMOS_PASSWORD.")
    parser.add_argument("--currency", default=os.getenv("ZOOMOS_CURRENCY", DEFAULT_CURRENCY), help="Валюта каталога, по умолчанию RUB.")
    parser.add_argument("--category", action="append", dest="categories", help="Категория B2B. Можно указать несколько раз.")
    parser.add_argument("--output", default=f"data/zoomos_devices_{timestamp}.csv", help="Путь к CSV-файлу результата.")
    parser.add_argument("--json-output", default="", help="Дополнительно сохранить JSON по указанному пути.")
    parser.add_argument("--limit-pages", type=int, default=0, help="Ограничить число страниц на категорию для быстрой проверки.")
    parser.add_argument("--sleep", type=float, default=0.2, help="Пауза между страницами в секундах.")
    return parser.parse_args()


def main() -> int:
    load_dotenv(Path(".env"))
    args = parse_args()

    username = args.username or os.getenv("ZOOMOS_USERNAME")
    password = args.password or os.getenv("ZOOMOS_PASSWORD")

    if not username:
        username = input("Zoomos login: ").strip()
    if not password:
        password = getpass.getpass("Zoomos password: ")

    categories = args.categories or list(DEFAULT_CATEGORIES)
    client = ZoomosClient(username=username, password=password)

    try:
        print("Авторизация в Zoomos...")
        client.login()
        print("Авторизация успешна.")

        all_rows: list[ProductRow] = []
        for category in categories:
            print(f"Категория: {category}")
            pager_id, shop_id, page_size, total_pages, found, first_rows = client.init_category(category, args.currency)
            if args.limit_pages:
                total_pages = min(total_pages, args.limit_pages)

            found_part = f"заявлено сайтом: {found}; " if found else ""
            print(f"  {found_part}строк на первой странице: {len(first_rows)}; страниц к сбору: {total_pages}")
            all_rows.extend(first_rows)

            empty_pages = 0
            for page_index in range(1, total_pages):
                rows = client.fetch_component_page(pager_id, shop_id, args.currency, page_index, category)
                print(f"  страница {page_index + 1}/{total_pages}: {len(rows)} строк")
                all_rows.extend(rows)

                if rows:
                    empty_pages = 0
                else:
                    empty_pages += 1
                if empty_pages >= 2:
                    break
                if args.sleep > 0:
                    time.sleep(args.sleep)

        rows = unique_rows(all_rows)
        output = Path(args.output)
        write_csv(rows, output)
        print(f"Готово: {output} ({len(rows)} строк)")

        if args.json_output:
            json_output = Path(args.json_output)
            write_json(rows, json_output)
            print(f"JSON: {json_output}")

        return 0

    except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
