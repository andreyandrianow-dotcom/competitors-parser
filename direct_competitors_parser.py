#!/usr/bin/env python3
"""Parse competitor catalogues directly from public competitor sites.

The output intentionally keeps the same core shape as ``zoomos_parser.py``:
product/category/device fields, product ids, prices, links, and a source page.
Competitor-specific columns are added at the front, plus fetch status columns
so protected pages are visible instead of being silently skipped.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.client import IncompleteRead
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from http.cookiejar import CookieJar

from zoomos_parser import USER_AGENT


DEFAULT_SITES = ("dns", "mts", "beeline", "megafon")
DEFAULT_CURRENCY = "RUB"

COMPETITOR_LABELS = {
    "dns": ("dns-shop.ru", "ДНС"),
    "mts": ("shop.mts.ru", "МТС"),
    "beeline": ("beeline.ru", "Билайн"),
    "megafon": ("shop.megafon.ru", "Мегафон"),
}

DEVICE_URL_MARKERS = (
    "smartfon",
    "smartfony",
    "smartphone",
    "iphone",
    "planshet",
    "plansety",
    "planset",
    "tablet",
    "ipad",
    "mobilnyj-telefon",
    "mobilnyy-telefon",
    "mobilnyi-telefon",
    "sotovyj-telefon",
    "knopocnyj",
    "knopochnyj",
    "knopocnye",
    "knopochnye",
)

ACCESSORY_URL_MARKERS = (
    "nakladka",
    "cehol",
    "chehol",
    "steklo",
    "plenka",
    "plenka",
    "kabel",
    "adapter",
    "zaryad",
    "akkumulator",
    "holder",
    "derzatel",
    "zasitnoe",
    "zaschitnoe",
    "remesok",
    "braslet",
    "nausniki",
    "kolonka",
    "sim-karta",
    "tarif",
    "monitor",
    "noutbuk",
    "graficeskij",
    "graficheskij",
    "graphic",
    "stilus",
    "stylus",
    "pencil",
)

KNOWN_BRANDS = (
    "Apple",
    "Samsung",
    "Xiaomi",
    "Redmi",
    "POCO",
    "Honor",
    "HONOR",
    "Huawei",
    "Realme",
    "Tecno",
    "Infinix",
    "OPPO",
    "Vivo",
    "OnePlus",
    "Google",
    "Nothing",
    "Nokia",
    "Alcatel",
    "BQ",
    "Blackview",
    "Doogee",
    "Itel",
    "ZTE",
    "Motorola",
    "Sony",
    "Lenovo",
    "Topdevice",
    "Digma",
    "TCL",
    "Meizu",
    "Nubia",
    "iQOO",
)


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    text: str
    error: str = ""


@dataclass
class DiscoveredUrl:
    url: str
    source_page: str
    name: str = ""
    brand: str = ""
    price: str = ""
    currency: str = DEFAULT_CURRENCY
    availability: str = ""
    category: str = ""
    product_id: str = ""


@dataclass
class DirectCompetitorRow:
    scraped_at: str
    competitor: str
    competitor_name: str
    category: str
    device_type: str
    product_id: str
    spra_id: str
    name: str
    brand: str
    supplier: str
    supplier_item_id: str
    wholesale_price: str
    retail_price: str
    currency: str
    availability: str
    item_code: str
    updated_at: str
    product_url: str
    supplier_url: str
    source_page: str
    fetch_status: str
    error: str


class HttpClient:
    def __init__(self, timeout: int = 45) -> None:
        self.timeout = timeout
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))

    def get(self, url: str) -> FetchResult:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.6,en;q=0.5",
            "Cache-Control": "no-cache",
        }
        request = Request(url, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                read_error = ""
                try:
                    raw = response.read()
                except IncompleteRead as exc:
                    raw = exc.partial
                    read_error = f"IncompleteRead: {len(raw)} bytes read"
                charset = response.headers.get_content_charset() or "utf-8"
                return FetchResult(
                    url=url,
                    final_url=response.geturl(),
                    status_code=getattr(response, "status", response.code),
                    text=raw.decode(charset, errors="replace"),
                    error=read_error,
                )
        except HTTPError as exc:
            raw = exc.read()
            charset = exc.headers.get_content_charset() if exc.headers else None
            return FetchResult(
                url=url,
                final_url=exc.geturl() or url,
                status_code=exc.code,
                text=raw.decode(charset or "utf-8", errors="replace"),
                error=str(exc),
            )
        except (URLError, TimeoutError, OSError) as exc:
            return FetchResult(url=url, final_url=url, status_code=0, text="", error=str(exc))


@dataclass
class JsonLdProduct:
    name: str = ""
    brand: str = ""
    price: str = ""
    currency: str = DEFAULT_CURRENCY
    availability: str = ""
    url: str = ""
    sku: str = ""


def clean_text(value: str) -> str:
    value = re.sub(r"<script\b.*?</script>", " ", value or "", flags=re.S | re.I)
    value = re.sub(r"<style\b.*?</style>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def normalize_number(value: object) -> str:
    text = str(value or "").replace("\xa0", " ")
    text = re.sub(r"[^\d,.\-]", "", text)
    text = text.replace(",", ".")
    if text.endswith(".0"):
        text = text[:-2]
    return text


def normalize_currency(value: str) -> str:
    value = (value or "").upper().strip()
    if value in {"RUR", "RUB", "₽", "РУБ", "РУБ."}:
        return "RUB"
    return value or DEFAULT_CURRENCY


def normalize_availability(value: str) -> str:
    value = value or ""
    value_l = value.lower()
    if "instock" in value_l or "в наличии" in value_l:
        return "true"
    if "outofstock" in value_l or "нет в наличии" in value_l:
        return "false"
    return value


def find_first(pattern: str, text: str, default: str = "", flags: int = re.S | re.I) -> str:
    match = re.search(pattern, text or "", flags)
    return html.unescape(match.group(1)).strip() if match else default


def extract_sitemap_locs(xml_text: str) -> list[str]:
    return [html.unescape(item).strip() for item in re.findall(r"<loc>\s*([^<]+)", xml_text or "", flags=re.I)]


def is_qrator_block(fetch: FetchResult) -> bool:
    body_l = (fetch.text or "").lower()
    return fetch.status_code == 401 and ("qrator" in body_l or "__qrator" in body_l)


def looks_like_device_url(url: str, include_all_products: bool = False) -> bool:
    if include_all_products:
        return True
    url_l = unquote(url).lower()
    if any(marker in url_l for marker in ACCESSORY_URL_MARKERS):
        return False
    return any(marker in url_l for marker in DEVICE_URL_MARKERS)


def looks_like_device_text(category: str, name: str, include_all_products: bool = False) -> bool:
    if include_all_products:
        return True
    text = f"{category} {name}".lower()
    accessory_markers = (
        "чехол",
        "стекло",
        "пленка",
        "плёнка",
        "кабель",
        "адаптер",
        "заряд",
        "держатель",
        "наушник",
        "колонка",
        "монитор",
        "ноутбук",
        "графическ",
        "graficesk",
        "graphic",
    )
    if any(marker in text for marker in accessory_markers):
        return False
    return any(
        marker in text
        for marker in (
            "смартф",
            "iphone",
            "планш",
            "ipad",
            "мобильный телефон",
            "кнопоч",
        )
    )


def classify_device(category: str, name: str, url: str = "") -> str:
    text = f"{category} {name} {url}".lower()
    if "планш" in text or "planshet" in text or "planset" in text or "tablet" in text or "ipad" in text:
        return "tablet"
    if (
        "кнопоч" in text
        or "мобильный телефон" in text
        or "mobilnyj-telefon" in text
        or "mobilnyy-telefon" in text
        or "mobilnyi-telefon" in text
        or "sotovyj-telefon" in text
    ):
        return "feature_phone"
    if (
        "смартф" in text
        or "smartfon" in text
        or "smartphone" in text
        or "iphone" in text
        or "galaxy" in text
        or "redmi" in text
        or "poco" in text
    ):
        return "smartphone"
    return "device"


def category_from_product(name: str, url: str, category_hint: str = "") -> str:
    if category_hint:
        return category_hint
    device_type = classify_device("", name, url)
    if device_type == "tablet":
        return "Планшет"
    if device_type == "smartphone":
        return "Смартфон"
    if device_type == "feature_phone":
        return "Телефон"
    return "Номенклатура"


def extract_brand(name: str, explicit: str = "") -> str:
    if explicit:
        return explicit
    name_l = (name or "").lower()
    for brand in KNOWN_BRANDS:
        if re.search(rf"(^|\s){re.escape(brand.lower())}($|\s|\d)", name_l):
            return brand
    return ""


def strip_title_noise(title: str) -> str:
    title = clean_text(title)
    title = re.sub(r"^\s*Купить\s+", "", title, flags=re.I)
    title = re.split(r"\s+по выгодной цене|\s+–\s+купить|\s+-\s+описание|\s+\|\s+Цены|\s+в интернет-магазине", title, maxsplit=1)[0]
    return title.strip(" -|")


def sanitize_url(value: str, base_url: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\\u002F", "/").replace("\\/", "/")
    value = value.strip().strip("\"' ,;\\")
    value = re.sub(r"/promo/?$", "/", value)
    if value.startswith("//"):
        value = "https:" + value
    value = urljoin(base_url, value)
    if value.startswith("http://moskva.beeline.ru/"):
        value = "https://" + value[len("http://") :]
    return value


def extract_product_id(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    for pattern in (
        r"/product/([0-9a-f]{8,})/",
        r"/(?:mobile|planshet)/(\d+)$",
        r"/shop/details/([^/]+)$",
        r"/product/([^/]+)$",
    ):
        match = re.search(pattern, path)
        if match:
            return match.group(1)
    return path.rsplit("/", 1)[-1]


def name_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    slug = ""
    if "product" in parts and parts[-1] != "product":
        slug = parts[-1]
    elif "details" in parts and parts[-1] != "details":
        slug = parts[-1]
    elif len(parts) >= 2 and parts[-1].isdigit():
        slug = parts[-1]
    else:
        slug = parts[-1] if parts else ""

    slug = unquote(slug)
    if slug.isdigit():
        return slug

    text = slug.replace("-", " ")
    replacements = {
        "smartfon": "Смартфон",
        "planshet": "Планшет",
        "planset": "Планшет",
        "mobilnyj telefon": "Мобильный телефон",
        "mobilnyy telefon": "Мобильный телефон",
        "gb": "ГБ",
        "tb": "ТБ",
    }
    for src, dst in replacements.items():
        text = re.sub(rf"\b{re.escape(src)}\b", dst, text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def json_loads_loose(raw: str) -> object | None:
    raw = html.unescape(raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    raw = raw.replace("\\/", "/")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def iter_jsonld_values(value: object) -> Iterable[object]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from iter_jsonld_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_jsonld_values(item)


def type_is_product(value: object) -> bool:
    if isinstance(value, list):
        return any(type_is_product(item) for item in value)
    return str(value).lower() == "product"


def parse_offer(offers: object) -> tuple[str, str, str, str]:
    if isinstance(offers, list):
        for offer in offers:
            price, currency, availability, url = parse_offer(offer)
            if price or availability or url:
                return price, currency, availability, url
        return "", DEFAULT_CURRENCY, "", ""
    if not isinstance(offers, dict):
        return "", DEFAULT_CURRENCY, "", ""

    price = normalize_number(offers.get("price") or offers.get("lowPrice") or offers.get("highPrice"))
    currency = normalize_currency(str(offers.get("priceCurrency") or DEFAULT_CURRENCY))
    availability = normalize_availability(str(offers.get("availability") or ""))
    url = str(offers.get("url") or "")
    return price, currency, availability, url


def parse_brand(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    if isinstance(value, list):
        for item in value:
            brand = parse_brand(item)
            if brand:
                return brand
        return ""
    return str(value or "").strip()


def extract_jsonld_products(html_text: str) -> list[JsonLdProduct]:
    products: list[JsonLdProduct] = []
    script_re = re.compile(
        r"<script\b[^>]+type=['\"]application/ld\+json['\"][^>]*>(.*?)</script>",
        flags=re.S | re.I,
    )
    for raw_script in script_re.findall(html_text or ""):
        data = json_loads_loose(raw_script)
        if data is None:
            continue

        for item in iter_jsonld_values(data):
            if not isinstance(item, dict) or not type_is_product(item.get("@type")):
                continue

            name = clean_text(str(item.get("name") or ""))
            if not name:
                continue

            price, currency, availability, offer_url = parse_offer(item.get("offers"))
            product = JsonLdProduct(
                name=name,
                brand=parse_brand(item.get("brand")),
                price=price,
                currency=currency,
                availability=availability,
                url=str(item.get("url") or offer_url or "").strip(),
                sku=str(item.get("sku") or item.get("mpn") or "").strip(),
            )
            products.append(product)

    unique: OrderedDict[tuple[str, str, str], JsonLdProduct] = OrderedDict()
    for product in products:
        key = (product.url, product.sku, product.name)
        unique.setdefault(key, product)
    return list(unique.values())


def extract_links(html_text: str, base_url: str) -> list[str]:
    links: list[str] = []
    for pattern in (
        r"href=['\"]([^'\"]+)['\"]",
        r'"(https?://[^"]+)"',
        r'"((?:/shop|/mobile|/planshet)[^"]+)"',
        r"(//moscow\.shop\.megafon\.ru/(?:mobile|planshet)/\d+)",
        r"(/shop/details/[^\"'<>\s\\]+)",
    ):
        for raw in re.findall(pattern, html_text or "", flags=re.I):
            links.append(sanitize_url(raw, base_url))
    return links


def extract_inline_catalog_products(html_text: str, base_url: str) -> list[DiscoveredUrl]:
    products: list[DiscoveredUrl] = []

    for match in re.finditer(r"href=['\"](/shop/details/[^/'\"]+/?)['\"]", html_text or "", flags=re.I):
        product_url = sanitize_url(match.group(1), base_url)
        start = max(0, match.start() - 2600)
        end = min(len(html_text), match.end() + 1200)
        chunk = html_text[start:end]

        name = clean_text(find_first(r"<p\b[^>]*>(.*?)</p>", html_text[match.end() : end]))
        if not name:
            name = name_from_url(product_url)

        price = normalize_number(find_first(r"amount=['\"]([0-9][0-9\s.,]*)['\"]", chunk))
        product_id = find_first(r"data-identity=['\"]([^'\"]+)['\"]", chunk) or extract_product_id(product_url)
        products.append(
            DiscoveredUrl(
                url=product_url,
                source_page=base_url,
                name=name,
                brand=extract_brand(name),
                price=price,
                currency=DEFAULT_CURRENCY,
                availability="true" if price else "",
                category=category_from_product(name, product_url),
                product_id=product_id,
            )
        )

    unique: OrderedDict[str, DiscoveredUrl] = OrderedDict()
    for product in products:
        unique.setdefault(product.url, product)
    return list(unique.values())


def add_discovered(
    products: OrderedDict[str, DiscoveredUrl],
    discovered: DiscoveredUrl,
    include_all_products: bool,
) -> None:
    if include_all_products:
        products.setdefault(discovered.url, discovered)
        return

    url_l = unquote(discovered.url).lower()
    text = f"{discovered.category} {discovered.name or name_from_url(discovered.url)}".lower()
    if any(marker in url_l for marker in ACCESSORY_URL_MARKERS):
        return
    if any(
        marker in text
        for marker in (
            "чехол",
            "стекло",
            "пленк",
            "кабель",
            "монитор",
            "ноутбук",
            "графическ",
            "graficesk",
            "graphic",
            "стилус",
            "stylus",
            "pencil",
        )
    ):
        return

    url_ok = looks_like_device_url(discovered.url, include_all_products=False)
    text_ok = looks_like_device_text(
        discovered.category,
        discovered.name or name_from_url(discovered.url),
        include_all_products=False,
    )
    if not (url_ok or text_ok):
        return
    products.setdefault(discovered.url, discovered)


def discover_dns(client: HttpClient, limit_urls: int, include_all_products: bool) -> list[DiscoveredUrl]:
    index = client.get("https://www.dns-shop.ru/sitemap.xml")
    sitemap_urls = [url for url in extract_sitemap_locs(index.text) if "sitemap-products" in url]
    products: OrderedDict[str, DiscoveredUrl] = OrderedDict()
    for sitemap_url in sitemap_urls:
        sitemap = client.get(sitemap_url)
        for product_url in extract_sitemap_locs(sitemap.text):
            if "/product/" not in product_url:
                continue
            discovered = DiscoveredUrl(
                url=product_url,
                source_page=sitemap_url,
                name=name_from_url(product_url),
                product_id=extract_product_id(product_url),
            )
            add_discovered(products, discovered, include_all_products)
            if limit_urls and len(products) >= limit_urls:
                return list(products.values())
    return list(products.values())


def discover_mts(client: HttpClient, limit_urls: int, include_all_products: bool) -> list[DiscoveredUrl]:
    index = client.get("https://shop.mts.ru/sitemap.xml")
    sitemap_urls = [
        url
        for url in extract_sitemap_locs(index.text)
        if re.search(r"/product_\d+\.xml$", url)
    ]
    products: OrderedDict[str, DiscoveredUrl] = OrderedDict()
    for sitemap_url in sitemap_urls:
        sitemap = client.get(sitemap_url)
        for product_url in extract_sitemap_locs(sitemap.text):
            if "/product/" not in product_url:
                continue
            if re.search(r"/(specs|accessories|reviews)/?$", product_url):
                continue
            discovered = DiscoveredUrl(
                url=product_url,
                source_page=sitemap_url,
                name=name_from_url(product_url),
                product_id=extract_product_id(product_url),
            )
            add_discovered(products, discovered, include_all_products)
            if limit_urls and len(products) >= limit_urls:
                return list(products.values())
    return list(products.values())


def catalog_bfs(
    client: HttpClient,
    seeds: Iterable[str],
    product_patterns: Iterable[str],
    catalog_pattern: str,
    limit_urls: int,
    max_catalog_pages: int,
    include_all_products: bool,
) -> list[DiscoveredUrl]:
    products: OrderedDict[str, DiscoveredUrl] = OrderedDict()
    queue = deque(seeds)
    visited: set[str] = set()
    product_res = [re.compile(pattern, flags=re.I) for pattern in product_patterns]
    catalog_re = re.compile(catalog_pattern, flags=re.I)

    while queue and len(visited) < max_catalog_pages:
        page_url = queue.popleft()
        if page_url in visited:
            continue
        visited.add(page_url)
        fetch = client.get(page_url)
        if fetch.status_code >= 400 or (fetch.error and not fetch.text):
            continue

        for product in extract_jsonld_products(fetch.text):
            product_url = sanitize_url(product.url, page_url)
            if not any(pattern.search(product_url) for pattern in product_res):
                continue
            discovered = DiscoveredUrl(
                url=product_url,
                source_page=page_url,
                name=product.name,
                brand=product.brand,
                price=product.price,
                currency=product.currency,
                availability=product.availability,
                category=category_from_product(product.name, product_url),
                product_id=product.sku or extract_product_id(product_url),
            )
            add_discovered(products, discovered, include_all_products)
            if limit_urls and len(products) >= limit_urls:
                return list(products.values())

        for discovered in extract_inline_catalog_products(fetch.text, page_url):
            if not any(pattern.search(discovered.url) for pattern in product_res):
                continue
            add_discovered(products, discovered, include_all_products)
            if limit_urls and len(products) >= limit_urls:
                return list(products.values())

        for link in extract_links(fetch.text, page_url):
            if any(pattern.search(link) for pattern in product_res):
                discovered = DiscoveredUrl(
                    url=link,
                    source_page=page_url,
                    name=name_from_url(link),
                    product_id=extract_product_id(link),
                )
                add_discovered(products, discovered, include_all_products)
                if limit_urls and len(products) >= limit_urls:
                    return list(products.values())
            elif catalog_re.search(link) and link not in visited:
                queue.append(link)

    return list(products.values())


def discover_beeline(
    client: HttpClient,
    limit_urls: int,
    max_catalog_pages: int,
    include_all_products: bool,
) -> list[DiscoveredUrl]:
    seeds = (
        "https://moskva.beeline.ru/shop/catalog/telefony/",
        "https://moskva.beeline.ru/shop/catalog/telefony/smartfony/",
        "https://moskva.beeline.ru/shop/catalog/planshety/",
        "https://moskva.beeline.ru/shop/catalog/planshety/planshety-3/",
    )
    return catalog_bfs(
        client=client,
        seeds=seeds,
        product_patterns=(r"moskva\.beeline\.ru/shop/details/[^/]+/?$",),
        catalog_pattern=r"moskva\.beeline\.ru/shop/catalog/(?:telefony|planshety)/",
        limit_urls=limit_urls,
        max_catalog_pages=max_catalog_pages,
        include_all_products=include_all_products,
    )


def discover_megafon(
    client: HttpClient,
    limit_urls: int,
    max_catalog_pages: int,
    include_all_products: bool,
) -> list[DiscoveredUrl]:
    seeds = (
        "https://moscow.shop.megafon.ru/mobile",
        "https://moscow.shop.megafon.ru/mobile/apple",
        "https://moscow.shop.megafon.ru/mobile/samsung",
        "https://moscow.shop.megafon.ru/mobile/xiaomi",
        "https://moscow.shop.megafon.ru/mobile/honor",
        "https://moscow.shop.megafon.ru/mobile/huawei",
        "https://moscow.shop.megafon.ru/mobile/realme",
        "https://moscow.shop.megafon.ru/mobile/tecno",
        "https://moscow.shop.megafon.ru/mobile/infinix",
        "https://moscow.shop.megafon.ru/mobile/poco",
        "https://moscow.shop.megafon.ru/planshet",
    )
    return catalog_bfs(
        client=client,
        seeds=seeds,
        product_patterns=(r"moscow\.shop\.megafon\.ru/(?:mobile|planshet)/\d+/?$",),
        catalog_pattern=r"moscow\.shop\.megafon\.ru/(?:mobile|planshet)(?:/[^?#]+)?/?$",
        limit_urls=limit_urls,
        max_catalog_pages=max_catalog_pages,
        include_all_products=include_all_products,
    )


def parse_meta_product(fetch: FetchResult) -> JsonLdProduct:
    title = find_first(r"<title[^>]*>(.*?)</title>", fetch.text)
    name = strip_title_noise(title)
    description = find_first(r"<meta[^>]+name=['\"]description['\"][^>]+content=['\"]([^'\"]+)", fetch.text)
    canonical = find_first(r"<link[^>]+rel=['\"]canonical['\"][^>]+href=['\"]([^'\"]+)", fetch.text)
    og_title = find_first(r"<meta[^>]+property=['\"]og:title['\"][^>]+content=['\"]([^'\"]+)", fetch.text)

    price = ""
    for pattern in (
        r'"price"\s*:\s*"?([0-9][0-9\s.,]*)"?',
        r"цена\s+([0-9\s]+)\s*руб",
        r"([0-9][0-9\s]{2,})\s*руб",
    ):
        price = normalize_number(find_first(pattern, fetch.text))
        if price:
            break

    if not name and og_title:
        name = strip_title_noise(og_title)
    if not name:
        name = name_from_url(fetch.final_url or fetch.url)

    return JsonLdProduct(
        name=name,
        brand=extract_brand(name),
        price=price,
        currency=DEFAULT_CURRENCY,
        availability=normalize_availability(find_first(r'"availability"\s*:\s*"([^"]+)"', fetch.text)),
        url=canonical or fetch.final_url or fetch.url,
    )


def merge_product_data(hint: DiscoveredUrl, fetch: FetchResult) -> JsonLdProduct:
    products = extract_jsonld_products(fetch.text)
    product = products[0] if products else parse_meta_product(fetch)

    return JsonLdProduct(
        name=product.name or hint.name or name_from_url(hint.url),
        brand=extract_brand(product.name or hint.name, product.brand or hint.brand),
        price=product.price or hint.price,
        currency=normalize_currency(product.currency or hint.currency),
        availability=normalize_availability(product.availability or hint.availability),
        url=sanitize_url(product.url or hint.url, hint.url),
        sku=product.sku or hint.product_id or extract_product_id(hint.url),
    )


def build_row(
    site: str,
    hint: DiscoveredUrl,
    product: JsonLdProduct,
    fetch_status: str,
    error: str = "",
) -> DirectCompetitorRow:
    competitor, competitor_name = COMPETITOR_LABELS[site]
    product_url = product.url or hint.url
    product_id = product.sku or hint.product_id or extract_product_id(product_url)
    name = product.name or hint.name or name_from_url(product_url)
    category = category_from_product(name, product_url, hint.category)
    brand = extract_brand(name, product.brand or hint.brand)
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return DirectCompetitorRow(
        scraped_at=scraped_at,
        competitor=competitor,
        competitor_name=competitor_name,
        category=category,
        device_type=classify_device(category, name, product_url),
        product_id=product_id,
        spra_id="",
        name=name,
        brand=brand,
        supplier=competitor_name,
        supplier_item_id=product_id,
        wholesale_price="",
        retail_price=normalize_number(product.price),
        currency=normalize_currency(product.currency or hint.currency),
        availability=normalize_availability(product.availability or hint.availability),
        item_code=product_id,
        updated_at="",
        product_url=product_url,
        supplier_url="",
        source_page=hint.source_page,
        fetch_status=fetch_status,
        error=error,
    )


def row_from_fetch(site: str, client: HttpClient, hint: DiscoveredUrl) -> tuple[DirectCompetitorRow, bool]:
    fetch = client.get(hint.url)
    if is_qrator_block(fetch):
        product = JsonLdProduct(
            name=hint.name or name_from_url(hint.url),
            brand=extract_brand(hint.name, hint.brand),
            price=hint.price,
            currency=hint.currency,
            availability=hint.availability,
            url=hint.url,
            sku=hint.product_id or extract_product_id(hint.url),
        )
        return build_row(site, hint, product, "blocked_qrator", "QRator challenge instead of product page"), True

    if fetch.status_code == 0:
        product = JsonLdProduct(
            name=hint.name or name_from_url(hint.url),
            brand=extract_brand(hint.name, hint.brand),
            price=hint.price,
            currency=hint.currency,
            availability=hint.availability,
            url=hint.url,
            sku=hint.product_id or extract_product_id(hint.url),
        )
        return build_row(site, hint, product, "fetch_error", fetch.error), False

    if fetch.status_code >= 400:
        product = JsonLdProduct(
            name=hint.name or name_from_url(hint.url),
            brand=extract_brand(hint.name, hint.brand),
            price=hint.price,
            currency=hint.currency,
            availability=hint.availability,
            url=hint.url,
            sku=hint.product_id or extract_product_id(hint.url),
        )
        return build_row(site, hint, product, f"http_{fetch.status_code}", fetch.error), False

    product = merge_product_data(hint, fetch)
    status = "ok" if product.name and product.price else "parsed_partial"
    return build_row(site, hint, product, status), False


def row_from_hint(site: str, hint: DiscoveredUrl, status: str, error: str = "") -> DirectCompetitorRow:
    product = JsonLdProduct(
        name=hint.name or name_from_url(hint.url),
        brand=extract_brand(hint.name, hint.brand),
        price=hint.price,
        currency=hint.currency,
        availability=hint.availability,
        url=hint.url,
        sku=hint.product_id or extract_product_id(hint.url),
    )
    return build_row(site, hint, product, status, error)


def unique_rows(rows: Iterable[DirectCompetitorRow]) -> list[DirectCompetitorRow]:
    result: list[DirectCompetitorRow] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.competitor, row.product_url)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def write_csv(rows: list[DirectCompetitorRow], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(asdict(rows[0]).keys()) if rows else list(DirectCompetitorRow.__dataclass_fields__)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_json(rows: list[DirectCompetitorRow], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2), encoding="utf-8")


def discover_site(
    site: str,
    client: HttpClient,
    limit_urls: int,
    max_catalog_pages: int,
    include_all_products: bool,
) -> list[DiscoveredUrl]:
    if site == "dns":
        return discover_dns(client, limit_urls, include_all_products)
    if site == "mts":
        return discover_mts(client, limit_urls, include_all_products)
    if site == "beeline":
        return discover_beeline(client, limit_urls, max_catalog_pages, include_all_products)
    if site == "megafon":
        return discover_megafon(client, limit_urls, max_catalog_pages, include_all_products)
    raise ValueError(f"Неизвестный сайт: {site}")


def parse_args() -> argparse.Namespace:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parser = argparse.ArgumentParser(
        description="Прямой парсер номенклатуры и цен DNS, МТС, Мегафон, Билайн."
    )
    parser.add_argument("--site", action="append", choices=DEFAULT_SITES, help="Сайт для сбора. Можно указать несколько раз.")
    parser.add_argument("--output", default=f"data/direct_competitors_{timestamp}.csv", help="Путь к CSV-файлу.")
    parser.add_argument("--json-output", default="", help="Дополнительно сохранить JSON.")
    parser.add_argument("--limit-urls", type=int, default=0, help="Ограничить число карточек на каждый сайт.")
    parser.add_argument("--max-catalog-pages", type=int, default=160, help="Максимум страниц каталога для Билайна/Мегафона.")
    parser.add_argument("--sleep", type=float, default=0.2, help="Пауза между карточками в секундах.")
    parser.add_argument("--timeout", type=int, default=45, help="Таймаут запроса в секундах.")
    parser.add_argument("--all-products", action="store_true", help="Не фильтровать только смартфоны/планшеты/кнопочные телефоны.")
    parser.add_argument("--links-only", action="store_true", help="Собрать только ссылки без открытия карточек.")
    parser.add_argument(
        "--no-fast-blocked",
        action="store_true",
        help="Не ускорять DNS/МТС после обнаружения QRator; пытаться открыть каждую карточку.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sites = tuple(args.site or DEFAULT_SITES)
    client = HttpClient(timeout=args.timeout)
    rows: list[DirectCompetitorRow] = []

    for site in sites:
        competitor, competitor_name = COMPETITOR_LABELS[site]
        print(f"{competitor_name}: сбор ссылок с {competitor}...")
        discovered = discover_site(site, client, args.limit_urls, args.max_catalog_pages, args.all_products)
        print(f"  найдено ссылок: {len(discovered)}")

        site_blocked = False
        for index, hint in enumerate(discovered, start=1):
            if args.links_only:
                row = row_from_hint(site, hint, "discovered")
            elif hint.name and hint.price:
                row = row_from_hint(site, hint, "ok_from_catalog")
            elif site_blocked and site in {"dns", "mts"} and not args.no_fast_blocked:
                row = row_from_hint(site, hint, "blocked_qrator", "QRator challenge detected earlier for this site")
            else:
                row, blocked = row_from_fetch(site, client, hint)
                site_blocked = site_blocked or blocked

            rows.append(row)
            if index % 50 == 0 or index == len(discovered):
                print(f"  обработано карточек: {index}/{len(discovered)}")
            if args.sleep > 0 and not args.links_only and not site_blocked:
                time.sleep(args.sleep)

    rows = unique_rows(rows)
    rows.sort(key=lambda item: (item.competitor_name, item.category, item.brand, item.name, item.product_url))

    output = Path(args.output)
    write_csv(rows, output)
    print(f"Готово: {output} ({len(rows)} строк)")

    if args.json_output:
        json_output = Path(args.json_output)
        write_json(rows, json_output)
        print(f"JSON: {json_output}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Остановлено пользователем.", file=sys.stderr)
        raise SystemExit(130)
