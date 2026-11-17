"""Rank Wise analysis service.

Endpoints
  GET  /health
  POST /api/seo/run        {url?: str, sample?: bool}  -> {ok, audit} | {ok: false, message}
  GET  /api/seo/history                                -> {audits: [...]}
  GET  /api/seo/<audit_id>                             -> audit JSON | 404
  POST /api/audit, /api/sample                         (original endpoints, kept)

Environment
  DATABASE_URL     Supabase Postgres connection string (session pooler URI)
  ALLOWED_ORIGINS  comma-separated site origins allowed to call this API,
                   e.g. https://rajwinderkumar.github.io
  PORT             set by the host; defaults to 8090 locally
"""

import json
import logging
import os
from contextlib import contextmanager

import psycopg2
from flask import Flask, jsonify, request

from engine import AuditError, run_live, run_sample

app = Flask(__name__)
log = logging.getLogger("rank-wise")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_DEFAULT_ORIGINS = "http://localhost:8080,http://127.0.0.1:8080"
ALLOWED_ORIGINS = {
    o.strip().rstrip("/")
    for o in os.environ.get("ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",")
    if o.strip()
}


# ---------------------------------------------------------------- CORS
@app.after_request
def add_cors(resp):
    origin = (request.headers.get("Origin") or "").rstrip("/")
    if origin and ("*" in ALLOWED_ORIGINS or origin in ALLOWED_ORIGINS):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return ("", 204)


# ------------------------------------------------------------- database
@contextmanager
def db():
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        conn.close()


def save_audit(audit: dict) -> None:
    """Store an audit. A storage failure is logged but never blocks the result."""
    if not DATABASE_URL:
        log.warning("DATABASE_URL not set; audit %s not saved", audit.get("id"))
        return
    try:
        with db() as cur:
            cur.execute(
                """
                insert into audits (id, url, score, status, results, recommendations)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (id) do nothing
                """,
                (
                    audit["id"],
                    audit.get("fetchedUrl") or audit.get("url") or "",
                    int(audit.get("score", 0)),
                    str(audit.get("status", "")),
                    json.dumps(audit),
                    json.dumps(audit.get("recommendations", [])),
                ),
            )
    except Exception:
        log.exception("Could not save audit %s", audit.get("id"))


# ----------------------------------------------------------------- routes
@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "rank-wise"})


def _run(sample: bool, url: str):
    try:
        audit = run_sample() if sample else run_live(url)
    except AuditError as err:
        return jsonify({"ok": False, "message": str(err)})
    except Exception:
        log.exception("Unexpected audit failure")
        return jsonify(
            {"ok": False, "message": "The audit could not be completed. Please try another URL."}
        )
    save_audit(audit)
    return jsonify({"ok": True, "audit": audit})


@app.post("/api/seo/run")
def seo_run():
    data = request.get_json(silent=True) or {}
    return _run(data.get("sample") is True, str(data.get("url") or ""))


@app.get("/api/seo/history")
def seo_history():
    if not DATABASE_URL:
        return jsonify({"audits": [], "message": "Database not configured"}), 503
    with db() as cur:
        cur.execute(
            "select id, url, score, status, created_at from audits "
            "order by created_at desc limit 24"
        )
        rows = cur.fetchall()
    audits = [
        {
            "id": r[0],
            "url": r[1],
            "score": int(r[2]),
            "status": r[3],
            "createdAt": r[4].isoformat(),
        }
        for r in rows
    ]
    return jsonify({"audits": audits})


@app.get("/api/seo/<audit_id>")
def seo_audit(audit_id):
    if not DATABASE_URL:
        return jsonify({"message": "Database not configured"}), 503
    with db() as cur:
        cur.execute("select results from audits where id = %s limit 1", (audit_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({"message": "Audit not found"}), 404
    try:
        return jsonify(json.loads(row[0]))
    except ValueError:
        return jsonify({"message": "Audit not found"}), 404


# Original endpoints, unchanged in behaviour.
@app.post("/api/audit")
def audit():
    data = request.get_json(silent=True) or {}
    return _run(False, str(data.get("url") or ""))


@app.post("/api/sample")
def sample():
    return _run(True, "")


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8090")),
        threaded=True,
        use_reloader=False,
    )
