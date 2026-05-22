"""HTTP bridge for Apps Script and Cloud Scheduler.

The service is intentionally small: it authenticates the request and starts the
real parser as a Cloud Run Job when CLOUD_RUN_JOB_NAME is configured. Without a
job name it can run the parser synchronously, which is useful for local testing.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

import google.auth
from flask import Flask, jsonify, request
from googleapiclient.discovery import build

from cloud_service.run_update import moscow_now, update_prices


app = Flask(__name__)
CLOUD_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def request_payload() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


def token_from_request() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").strip()
    return (
        request.headers.get("X-Parser-Token", "").strip()
        or request.args.get("token", "").strip()
        or str(request_payload().get("token", "")).strip()
    )


def is_authorized() -> bool:
    expected = os.getenv("WEBHOOK_TOKEN", "").strip()
    if not expected:
        return True
    return hmac.compare_digest(token_from_request(), expected)


def bool_payload_value(name: str, default: bool = False) -> bool:
    value = request_payload().get(name, request.args.get(name, ""))
    if value in ("", None):
        return default
    return str(value).lower() in {"1", "true", "yes", "y", "да"}


def cloud_run_job_resource() -> str:
    configured = os.getenv("CLOUD_RUN_JOB_NAME", "").strip()
    if not configured:
        return ""
    if configured.startswith("projects/"):
        return configured
    project = (
        os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        or os.getenv("GCP_PROJECT", "").strip()
        or os.getenv("CLOUD_RUN_PROJECT", "").strip()
    )
    region = os.getenv("CLOUD_RUN_REGION", "europe-west1").strip()
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT or CLOUD_RUN_PROJECT is required for Cloud Run Job launch")
    return f"projects/{project}/locations/{region}/jobs/{configured}"


def start_cloud_run_job() -> dict[str, str] | None:
    job = cloud_run_job_resource()
    if not job:
        return None
    creds, _ = google.auth.default(scopes=CLOUD_SCOPES)
    service = build("run", "v2", credentials=creds, cache_discovery=False)
    operation = service.projects().locations().jobs().run(name=job, body={}).execute()
    return {
        "mode": "cloud_run_job",
        "job": job,
        "operation": operation.get("name", ""),
    }


@app.get("/health")
def health():
    return jsonify({"ok": True, "time": moscow_now()})


@app.post("/run")
def run_parser():
    if not is_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    try:
        launched = start_cloud_run_job()
        if launched:
            try:
                from cloud_service.sheets_writer import set_control_values

                set_control_values(
                    {
                        "runRequestedAt": moscow_now(),
                        "runSource": str(request_payload().get("source") or "webhook"),
                        "runStatus": "QUEUED",
                        "lastError": "",
                    }
                )
            except Exception as exc:  # The job still starts even if status pre-write fails.
                launched["statusWarning"] = str(exc)[:500]
            return jsonify({"ok": True, **launched}), 202

        result = update_prices(use_last_known_good=bool_payload_value("useLastKnownGood"))
        return jsonify(result)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)[:1000]}), 500
