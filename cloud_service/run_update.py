#!/usr/bin/env python3
"""Cloud entrypoint for a full competitor price update."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cloud_service.sheets_writer import apply_manifest, set_control_values


ROOT = Path(__file__).resolve().parents[1]


def moscow_now() -> str:
    return datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M:%S")


def run(cmd: list[str], timeout: int = 3600) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True, timeout=timeout)


def load_validation() -> dict[str, object]:
    return json.loads((ROOT / "data/direct_competitors_validation.json").read_text(encoding="utf-8"))


def update_prices(use_last_known_good: bool = False) -> dict[str, object]:
    started = moscow_now()
    warning = ""
    set_control_values(
        {
            "runStatus": "RUNNING",
            "runStartedAt": started,
            "lastError": "",
            "lastWarning": "",
        }
    )
    try:
        if not use_last_known_good:
            run(
                [
                    sys.executable,
                    "direct_competitors_parser.py",
                    "--site",
                    "beeline",
                    "--site",
                    "megafon",
                    "--output",
                    "data/direct_competitors_open_sites_fixed.csv",
                    "--json-output",
                    "data/direct_competitors_open_sites_fixed.json",
                ],
                timeout=3600,
            )
            run([sys.executable, "cloud_service/browser_price_scraper.py"], timeout=7200)
            run(
                [
                    sys.executable,
                    "build_final_competitors_result.py",
                    "--raw",
                    "data/direct_competitors_open_sites_fixed.csv",
                    "--output",
                    "data/direct_competitors_final_open_sites.csv",
                    "--json-output",
                    "data/direct_competitors_final_open_sites.json",
                ],
                timeout=600,
            )
            run(
                [
                    sys.executable,
                    "merge_priced_competitors_result.py",
                    "--base",
                    "data/direct_competitors_final_open_sites.csv",
                    "--priced",
                    "data/browser_dns_mts_prices.csv",
                    "--output",
                    "data/direct_competitors_final.csv",
                    "--json-output",
                    "data/direct_competitors_final.json",
                ],
                timeout=600,
            )
        else:
            warning = "used last known good"

        run(
            [
                sys.executable,
                "validate_competitors_result.py",
                "--input",
                "data/direct_competitors_final.csv",
                "--report",
                "data/direct_competitors_validation.json",
            ],
            timeout=300,
        )
        validation = load_validation()
        if validation.get("errors_count"):
            raise RuntimeError(f"validation failed: {validation.get('errors_count')} errors")

        run(
            [
                sys.executable,
                "scripts/build_google_sheet_batches.py",
                "--input",
                "data/direct_competitors_final.csv",
                "--output-dir",
                "outputs/google_sheet_batches",
            ],
            timeout=300,
        )
        manifest = apply_manifest(ROOT / "outputs/google_sheet_batches/manifest.json")
        counters = validation.get("counters", {})
        set_control_values(
            {
                "runStatus": "DONE",
                "runStartedAt": started,
                "updatedAt": str(manifest["updatedAt"]),
                "rows": str(manifest["rows"]),
                "lastError": "",
                "lastWarning": warning,
            }
        )
        return {
            "ok": True,
            "updatedAt": manifest["updatedAt"],
            "rows": manifest["rows"],
            "counters": counters,
            "warning": warning,
        }
    except Exception as exc:
        error = str(exc)[:1000]
        set_control_values(
            {
                "runStatus": "ERROR",
                "runStartedAt": started,
                "lastError": error,
                "lastWarning": warning,
            }
        )
        raise


def main() -> int:
    use_lkg = os.getenv("USE_LAST_KNOWN_GOOD", "").lower() in {"1", "true", "yes"}
    result = update_prices(use_last_known_good=use_lkg)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
