#!/usr/bin/env python3
"""Browser scraper for DNS and MTS catalog pages.

This module is intended for a cloud container with Playwright. It writes the
same final columns used by the business result table.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse


FIELDS = ["Конкурент", "Категория", "Наименование", "Цена", "Цена со скидкой", "Ссылка", "Якорь"]

DNS_CATEGORIES = [
    ("Смартфон", "https://www.dns-shop.ru/catalog/17a8a01d16404e77/smartfony/", 130),
    ("Планшет", "https://www.dns-shop.ru/catalog/17a8a05316404e77/plansety/", 80),
    ("Телефон", "https://www.dns-shop.ru/catalog/17a89fea16404e77/sotovye-telefony/", 50),
]
MTS_CATEGORIES = [
    ("Смартфон", "https://shop.mts.ru/catalog/smartfony/", 70),
    ("Планшет", "https://shop.mts.ru/catalog/gadzhety/planshety/", 50),
    ("Телефон", "https://shop.mts.ru/catalog/knopochnye-telefony/", 10),
]


def clean(value: str) -> str:
    return re.sub(r"[\s\xa0]+", " ", value or "").strip()


def prices_from_text(text: str) -> list[int]:
    values = []
    for match in re.finditer(r"([\d\s\xa0]+)\s*₽", text or ""):
        raw = re.sub(r"\D", "", match.group(1))
        if raw:
            price = int(raw)
            if price >= 1000:
                values.append(price)
    return values


def price_pair(values: list[int]) -> tuple[str, str]:
    unique = sorted(set(values))
    if not unique:
        return "", ""
    if len(unique) == 1:
        return str(unique[0]), ""
    return str(unique[-1]), str(unique[0])


def anchor(prefix: str, url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    if prefix == "dns-shop.ru" and "product" in parts:
        idx = parts.index("product")
        if len(parts) > idx + 1:
            return f"{prefix}:{parts[idx + 1]}"
    if prefix == "mts.ru" and "product" in parts:
        idx = parts.index("product")
        if len(parts) > idx + 1:
            return f"{prefix}:{parts[idx + 1]}"
    return f"{prefix}:{parts[-1] if parts else path}"


def is_accessory(name: str, url: str) -> bool:
    text = f"{name} {url}".lower()
    lead = re.match(r"\s*(чехол|защитн|стекло|пленк|кабель|заряд|адаптер|держатель|наушник|колонка|сим-карта|тариф|стилус|stylus|pencil)\b", text)
    if lead:
        return True
    if re.search(r"\b(смартфон|планшет|телефон)\b", name.lower()):
        return False
    return any(marker in text for marker in ("чехол", "стилус", "stylus", "pencil", "кабель", "заряд", "наушник"))


async def goto(page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:
        pass


async def scrape_dns_page(page, category: str, url: str) -> list[dict[str, str]]:
    await goto(page, url)
    try:
        await page.wait_for_selector(".catalog-product", timeout=20_000)
    except Exception:
        return []
    return await page.evaluate(
        """([category]) => {
          const clean = (v) => String(v || '').replace(/[\\s\\u00a0]+/g, ' ').trim();
          const prices = (text) => [...String(text || '').matchAll(/([\\d\\s\\u00a0]+)\\s*₽/g)]
            .map(m => Number(m[1].replace(/\\D/g, ''))).filter(n => n >= 1000);
          const out = [];
          for (const card of document.querySelectorAll('.catalog-product')) {
            const link = card.querySelector('a[href*="/product/"]');
            const nameNode = card.querySelector('.catalog-product__name, .product-buy__name, a[href*="/product/"]');
            const name = clean(nameNode?.innerText || link?.innerText || '');
            const href = link?.href || '';
            const vals = [...new Set(prices(card.innerText))].sort((a,b)=>a-b);
            if (!name || !href || !vals.length) continue;
            out.push({category, name, href, price: String(vals[vals.length - 1]), discount: vals.length > 1 ? String(vals[0]) : ''});
          }
          return out;
        }""",
        [category],
    )


async def scrape_mts_page(page, category: str, url: str) -> list[dict[str, str]]:
    await goto(page, url)
    try:
        await page.wait_for_selector(".product-card", timeout=20_000)
    except Exception:
        return []
    return await page.evaluate(
        """([category]) => {
          const clean = (v) => String(v || '').replace(/[\\s\\u00a0]+/g, ' ').trim();
          const priceNum = (v) => {
            const m = String(v || '').match(/([\\d\\s\\u00a0]+)\\s*₽/);
            return m ? m[1].replace(/\\D/g, '') : '';
          };
          function parsePrice(card) {
            const topOld = priceNum(card.querySelector('.product-card__old-price .price__value')?.innerText || '');
            const wrapper = card.querySelector('.product-card__price-group-wrapper') || card.querySelector('.product-card__price-group') || card;
            const defaultPrice = priceNum(wrapper.querySelector('.price-group__default-price .price__value')?.innerText || '');
            const noKitEl = [...wrapper.querySelectorAll('.price-group__old-price')].find(e => (e.innerText || '').includes('Без комплекта'));
            const noKitPrice = priceNum(noKitEl?.innerText || '');
            const oldPrices = [...wrapper.querySelectorAll('.price-group__old-price .price__value')].map(e => priceNum(e.innerText)).filter(Boolean);
            const oldPrice = oldPrices.find(p => p !== defaultPrice) || oldPrices[0] || '';
            if (topOld && noKitPrice) return { price: topOld, discount: noKitPrice };
            if (oldPrice && defaultPrice && Number(oldPrice) > Number(defaultPrice)) return { price: oldPrice, discount: defaultPrice };
            if (defaultPrice) return { price: defaultPrice, discount: '' };
            const vals = [...new Set([...(card.innerText || '').matchAll(/([\\d\\s\\u00a0]+)\\s*₽/g)]
              .map(m => Number(m[1].replace(/\\D/g, ''))).filter(n => n >= 1000))].sort((a,b)=>a-b);
            if (!vals.length) return {price:'', discount:''};
            return { price: String(vals[vals.length - 1]), discount: vals.length > 1 ? String(vals[0]) : '' };
          }
          const out = [];
          for (const card of document.querySelectorAll('.product-card')) {
            const link = card.querySelector('a.card-name[href*="/product/"], a[href*="/product/"]');
            const name = clean(link?.innerText || '');
            const href = link?.href || '';
            const p = parsePrice(card);
            if (!name || !href || !p.price) continue;
            out.push({category, name, href, price: p.price, discount: p.discount});
          }
          return out;
        }""",
        [category],
    )


async def collect_category(page, competitor: str, category: str, base_url: str, max_pages: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    empty_tail = 0
    for page_no in range(1, max_pages + 1):
        if competitor == "ДНС":
            url = base_url if page_no == 1 else f"{base_url}?p={page_no}"
            items = await scrape_dns_page(page, category, url)
            prefix = "dns-shop.ru"
        else:
            url = base_url if page_no == 1 else f"{base_url.rstrip('/')}/{page_no}/"
            items = await scrape_mts_page(page, category, url)
            prefix = "mts.ru"
        if not items:
            empty_tail += 1
            if empty_tail >= 3:
                break
            continue
        empty_tail = 0
        for item in items:
            if is_accessory(item["name"], item["href"]):
                continue
            rows.append(
                {
                    "Конкурент": competitor,
                    "Категория": item["category"],
                    "Наименование": item["name"],
                    "Цена": item["price"],
                    "Цена со скидкой": item["discount"],
                    "Ссылка": item["href"],
                    "Якорь": anchor(prefix, item["href"]),
                }
            )
    return rows


async def scrape_all() -> list[dict[str, str]]:
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError("Playwright is required for DNS/MTS browser scraping. Install cloud_service/requirements.txt.") from exc

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="ru-RU",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1365, "height": 900},
        )
        page = await context.new_page()
        rows: list[dict[str, str]] = []
        for category, url, max_pages in DNS_CATEGORIES:
            rows.extend(await collect_category(page, "ДНС", category, url, max_pages))
        for category, url, max_pages in MTS_CATEGORIES:
            rows.extend(await collect_category(page, "МТС", category, url, max_pages))
        await browser.close()
    by_key: OrderedDict[tuple[str, str], dict[str, str]] = OrderedDict()
    for row in rows:
        by_key.setdefault((row["Конкурент"], row["Ссылка"]), row)
    return sorted(by_key.values(), key=lambda row: (row["Конкурент"], row["Категория"], row["Наименование"], row["Ссылка"]))


def write_outputs(rows: list[dict[str, str]], csv_path: Path, json_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Собрать DNS/MTS с ценами через Playwright.")
    parser.add_argument("--output", default="data/browser_dns_mts_prices.csv")
    parser.add_argument("--json-output", default="data/browser_dns_mts_prices.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = asyncio.run(scrape_all())
    write_outputs(rows, Path(args.output), Path(args.json_output))
    print(f"DNS/MTS rows: {len(rows)}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
