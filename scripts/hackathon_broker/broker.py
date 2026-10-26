#!/usr/bin/env python3
"""Small SSH-invoked broker. Python standard library only."""
import argparse
from contextlib import closing
import getpass
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import warnings
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_URL = "https://api.alfahack-gateway26.ru/v1"
UPSTREAM_TIMEOUT_SECONDS = 90
MODELS = {
    "google/gemma-4-26b-a4b-it", "qwen/qwen3.6-35b-a3b", "z-ai/glm-5.3-flash",
    "deepseek/deepseek-v4.1-flash", "qwen/qwen3-vl-30b-a3b-instruct", "moonshotai/kimi-k2.6",
}
MAX_PROMPT = 12000
MAX_RESPONSE = 2_000_000
MAX_TOKENS = 512
MAX_DAILY = 4
HOME = Path.home()
CONFIG = HOME / ".config/alpha-hack-broker"
KEY_FILE = CONFIG / "upstream.key"
STATE = HOME / ".local/state/alpha-hack-broker"
DB = STATE / "jobs.sqlite3"


class BrokerError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def safe_json(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def emit(obj):
    print(safe_json(obj))


def ensure_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def read_key():
    try:
        key = KEY_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise BrokerError("credential_missing") from None
    if not key:
        raise BrokerError("credential_invalid")
    return key


def safe_text(value, key):
    text = str(value)
    if key:
        text = text.replace(key, "[REDACTED]")
    # Defensive filtering for accidentally echoed credential-shaped values.
    return re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1[REDACTED]", text)


def safe_usage(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "cached_tokens"):
        count = value.get(name)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            result[name] = count
    for source, target, field in (
        ("prompt_tokens_details", "prompt_cached_tokens", "cached_tokens"),
        ("completion_tokens_details", "completion_reasoning_tokens", "reasoning_tokens"),
        ("completion_tokens_details", "completion_cached_tokens", "cached_tokens"),
    ):
        details = value.get(source)
        count = details.get(field) if isinstance(details, dict) else None
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            result[target] = count
    return result or None


def configure(replace=False):
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise BrokerError("tty_required")
    ensure_dir(CONFIG)
    if KEY_FILE.exists() and not replace:
        raise BrokerError("already_configured_use_replace")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            value = getpass.getpass("API key (input hidden): ")
    except getpass.GetPassWarning:
        raise BrokerError("secure_input_unavailable") from None
    if (not value or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
            or "\r" in value or "\n" in value):
        raise BrokerError("credential_invalid")
    fd, tmp = tempfile.mkstemp(prefix=".upstream.", dir=CONFIG)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, KEY_FILE)
        os.chmod(KEY_FILE, 0o600)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    emit({"status": "configured"})


def health():
    present = False
    try:
        present = KEY_FILE.stat().st_size > 0
    except FileNotFoundError:
        pass
    emit({"status": "ok", "version": 2, "credential_configured": present,
          "request_policy": "deepseek_compact_no_thinking"})


def status():
    """Aggregate read-only quota metadata; never load credential or job content."""
    day = budget_day()
    result = {"status": "uninitialized", "budget_day": day, "daily_attempt_limit": MAX_DAILY,
              "max_output_tokens": MAX_TOKENS, "used_attempts": None,
              "remaining_attempts": None, "shared_rub_balance": None}
    if not DB.is_file():
        emit(result)
        return
    try:
        with closing(sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
            used = db.execute("SELECT COUNT(*) FROM jobs WHERE day=?", (day,)).fetchone()[0]
            blocked = bool(db.execute("SELECT 1 FROM day_limits WHERE day=? AND blocked=1", (day,)).fetchone())
    except sqlite3.Error:
        raise BrokerError("database_unavailable") from None
    result.update(status="ok", used_attempts=used, day_blocked=blocked,
                  remaining_attempts=0 if blocked else max(0, MAX_DAILY - used))
    emit(result)


def budget_day(now=None):
    now = now or datetime.now(timezone.utc)
    moscow = timezone(timedelta(hours=3))
    local = now.astimezone(moscow)
    boundary = local.replace(hour=3, minute=0, second=0, microsecond=0)
    if local < boundary:
        boundary -= timedelta(days=1)
    return boundary.date().isoformat()


def reserve(task_id, model, prompt, *, db_path=None):
    database = Path(db_path) if db_path is not None else DB
    ensure_dir(database.parent)
    fingerprint = hashlib.sha256(
        json.dumps({"model": model, "prompt": prompt}, ensure_ascii=False,
                   sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    with closing(sqlite3.connect(database, timeout=10)) as db, db:
        os.chmod(database, 0o600) if database.exists() else None
        db.execute("PRAGMA busy_timeout=10000")
        # WAL mode must be set outside a transaction, but can contend when two
        # processes cold-start the same database. SQLite may return BUSY here
        # without invoking its busy handler, so retry only this verified mode
        # transition within the same 10-second lock budget.
        wal_deadline = time.monotonic() + 10
        wal_delay = 0.01
        while True:
            try:
                mode = db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if mode.lower() != "wal":
                    raise BrokerError("database_unavailable")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= wal_deadline:
                    raise BrokerError("database_unavailable") from None
                time.sleep(wal_delay)
                wal_delay = min(wal_delay * 2, 0.25)
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS jobs (task_id TEXT PRIMARY KEY, day TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL, result TEXT)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        if "fingerprint" not in columns:
            # Preserve existing reservations; null fingerprints fail closed.
            db.execute("ALTER TABLE jobs ADD COLUMN fingerprint TEXT")
        db.execute("CREATE TABLE IF NOT EXISTS day_limits (day TEXT PRIMARY KEY, blocked INTEGER NOT NULL)")
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(database) + suffix)
            if sidecar.exists():
                os.chmod(sidecar, 0o600)
        old = db.execute("SELECT model,status,result,fingerprint FROM jobs WHERE task_id=?", (task_id,)).fetchone()
        if old:
            if old[0] != model or (old[3] is not None and old[3] != fingerprint):
                raise BrokerError("task_id_conflict")
            if old[1] == "completed" and old[2]:
                return "completed", old[2]
            return "pending", None
        day = budget_day()
        if db.execute("SELECT 1 FROM day_limits WHERE day=? AND blocked=1", (day,)).fetchone():
            raise BrokerError("daily_budget_blocked")
        count = db.execute("SELECT COUNT(*) FROM jobs WHERE day=?", (day,)).fetchone()[0]
        if count >= MAX_DAILY:
            raise BrokerError("daily_attempt_limit")
        db.execute("INSERT INTO jobs(task_id,day,model,status,fingerprint) VALUES(?,?,?,'reserved',?)", (task_id, day, model, fingerprint))
        db.commit()
        os.chmod(database, 0o600)
        return "new", None


def block_day():
    ensure_dir(DB.parent)
    with closing(sqlite3.connect(DB, timeout=10)) as db, db:
        day = budget_day()
        db.execute("CREATE TABLE IF NOT EXISTS day_limits (day TEXT PRIMARY KEY, blocked INTEGER NOT NULL)")
        db.execute("INSERT OR REPLACE INTO day_limits(day,blocked) VALUES(?,1)", (day,))


def store_result(task_id, status, result):
    with closing(sqlite3.connect(DB, timeout=10)) as db, db:
        db.execute("UPDATE jobs SET status=?,result=? WHERE task_id=?", (status, safe_json(result), task_id))


def validate(job):
    if not isinstance(job, dict) or set(job) != {"task_id", "model", "prompt"}:
        raise BrokerError("invalid_request")
    task, model, prompt = job["task_id"], job["model"], job["prompt"]
    if not isinstance(task, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", task):
        raise BrokerError("invalid_request")
    if model not in MODELS:
        raise BrokerError("model_not_allowed")
    if not isinstance(prompt, str) or len(prompt) > MAX_PROMPT:
        raise BrokerError("prompt_too_long" if isinstance(prompt, str) else "invalid_request")
    return task, model, prompt


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def run_job(job, *, key_path=None, opener=None, db_path=None):
    global KEY_FILE, DB
    orig_key, orig_db = KEY_FILE, DB
    if key_path is not None:
        KEY_FILE = Path(key_path)
    if db_path is not None:
        DB = Path(db_path)
    key = None
    task = model = None
    try:
        task, model, prompt = validate(job)
        # Fail before reserving quota or making any network request.
        key = read_key()
        prior, prior_result = reserve(task, model, prompt, db_path=DB)
        if prior == "completed" and prior_result:
            return json.loads(prior_result)
        if prior != "new":
            return {"task_id": task, "status": "error", "model": model,
                    "error": "previous_request_unresolved"}
        body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": MAX_TOKENS}
        if model == "deepseek/deepseek-v4.1-flash":
            # Fixed server policy for short structured responses. No user-selected
            # parameters and no increase of token/day quotas. Gateway support is
            # measured by the separately assigned bounded live pilot.
            body["reasoning_effort"] = "none"
        payload = json.dumps(body).encode()
        req = urllib.request.Request(BASE_URL + "/chat/completions", data=payload,
                                     headers={"Authorization": "Bearer " + key,
                                              "Content-Type": "application/json"}, method="POST")
        client = opener or urllib.request.build_opener(NoRedirect())
        start = time.monotonic()
        try:
            with client.open(req, timeout=UPSTREAM_TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise BrokerError("response_too_large")
                data = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(MAX_RESPONSE + 1).decode("utf-8", errors="ignore")[:MAX_RESPONSE].lower()
            finally:
                exc.close()
            budget = "budget has been exceeded" in body or "budget exceeded" in body
            if exc.code == 429 or budget:
                block_day()
            code = "budget_exceeded" if budget else {401: "upstream_unauthorized", 403: "upstream_forbidden", 429: "upstream_limited"}.get(exc.code, "upstream_error")
            result = {"task_id": task, "status": "error", "model": model, "error": code}
            store_result(task, "completed", result)
            return result
        except (TimeoutError, urllib.error.URLError) as exc:
            if isinstance(exc, urllib.error.URLError) and not isinstance(exc.reason, TimeoutError):
                raise BrokerError("upstream_error") from None
            result = {"task_id": task, "status": "error", "model": model,
                      "error": "upstream_timeout",
                      "duration_seconds": round(time.monotonic() - start, 3)}
            store_result(task, "completed", result)
            return result
        except json.JSONDecodeError:
            raise BrokerError("malformed_upstream_response") from None
        except Exception as exc:
            if isinstance(exc, BrokerError):
                raise
            raise BrokerError("upstream_error") from None
        try:
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage")
            if not isinstance(content, str):
                raise ValueError()
        except (TypeError, KeyError, IndexError, ValueError):
            raise BrokerError("malformed_upstream_response") from None
        if not content.strip():
            result = {"task_id": task, "status": "error", "model": model,
                      "error": "empty_model_content", "usage": safe_usage(usage),
                      "duration_seconds": round(time.monotonic() - start, 3)}
            finish = data["choices"][0].get("finish_reason")
            if finish in {"stop", "length", "content_filter", "tool_calls"}:
                result["finish_reason"] = finish
            store_result(task, "completed", result)
            return result
        result = {"task_id": task, "status": "ok", "model": model,
                  "content": safe_text(content, key),
                  "usage": safe_usage(usage),
                  "duration_seconds": round(time.monotonic() - start, 3)}
        store_result(task, "completed", result)
        return result
    except BrokerError as exc:
        return {"task_id": task, "status": "error", "model": model, "error": exc.code}
    finally:
        KEY_FILE, DB = orig_key, orig_db


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("configure", "health", "status", "run"))
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "configure":
            configure(args.replace)
        elif args.command == "health":
            health()
        elif args.command == "status":
            status()
        else:
            try:
                job = json.load(sys.stdin)
            except Exception:
                raise BrokerError("invalid_request") from None
            emit(run_job(job))
    except BrokerError as exc:
        emit({"status": "error", "error": exc.code})
    except Exception:
        emit({"status": "error", "error": "internal_error"})


if __name__ == "__main__":
    os.umask(0o077)
    main()
