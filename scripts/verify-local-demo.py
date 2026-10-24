"""Run the deterministic FastAPI demo over local HTTP with an isolated SQLite DB."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".tmp" / "demo-smoke"
TIMEOUT = 15


def request(base: str, path: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def check(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)
    print(f"PASS {message}")


def wait_health(base: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + TIMEOUT
    last_error = "server did not respond"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"uvicorn exited early ({process.returncode})")
        try:
            request(base, "/api/v1/health")
            return
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(0.1)
    raise TimeoutError(f"uvicorn health deadline exceeded: {last_error}")


def start(python: str, db_path: Path, enabled: bool) -> tuple[subprocess.Popen, str]:
    # Reserve and release an ephemeral loopback port immediately before launch.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # Uvicorn needs the Windows loader search path and system root, but must
    # not inherit arbitrary caller variables (which may contain credentials).
    env = {
        name: os.environ[name]
        for name in ("PATH", "SYSTEMROOT", "WINDIR")
        if name in os.environ
    }
    env["ALPHA_DB_PATH"] = str(db_path.resolve())
    env["ALPHA_DEMO_MODE"] = "1" if enabled else "0"
    log_path = db_path.with_suffix(".uvicorn.log")
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [python, "-m", "uvicorn", "app:app", "--app-dir", str(ROOT / "backend"),
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    # Parent can close its handle; child retains the redirected descriptor.
    log.close()
    base = f"http://127.0.0.1:{port}"
    try:
        wait_health(base, process)
    except BaseException:
        # The caller cannot register ownership until start returns. Clean up
        # here so readiness failures do not leak the newly launched server.
        stop(process)
        raise
    return process, base


def stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=TIMEOUT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=None, help="Python containing backend requirements")
    args = parser.parse_args()
    python = args.python or sys.executable
    if not Path(python).is_file():
        python = sys.executable
    BASE.mkdir(parents=True, exist_ok=True)
    run_dir = BASE / uuid.uuid4().hex
    run_dir.mkdir()
    disabled_db = run_dir / "disabled.sqlite3"
    demo_db = run_dir / "demo.sqlite3"
    processes: list[subprocess.Popen] = []
    try:
        proc, base = start(python, disabled_db, False)
        processes.append(proc)
        status, payload = request(base, "/api/v1/health")
        check(status == 200 and payload == {"status": "ok", "mode": "disabled"}, "disabled health")
        status, payload = request(base, "/api/v1/companies")
        check(status == 503 and payload["error"]["code"] == "auth_unconfigured", "disabled business access")
        stop(proc)
        processes.remove(proc)

        proc, base = start(python, demo_db, True)
        processes.append(proc)
        status, companies = request(base, "/api/v1/companies")
        check(status == 200 and len(companies["items"]) == 2, "allowlist has exactly two synthetic companies")
        status, denied = request(base, "/api/v1/companies/outside-company/feed?role_id=CFO")
        check(status == 403 and denied["error"]["code"] == "access_denied", "unknown company scope denied")

        runs = []
        for company in ("demo-trade-01", "demo-transport-01"):
            for role in ("CFO", "COO"):
                status, run = request(base, f"/api/v1/companies/{company}/analysis-runs", "POST",
                                      {"role_id": role, "idempotency_key": f"smoke-{company}-{role}"})
                check(status == 202 and run["execution_mode"] == "deterministic_demo", f"analysis {company}/{role}")
                runs.append(run)
        check(len({run["result_ids"][0] for run in runs}) == 4, "four company-role contexts produce distinct cards")

        first = runs[0]
        status, repeated = request(base, "/api/v1/companies/demo-trade-01/analysis-runs", "POST",
                                   {"role_id": "CFO", "idempotency_key": "smoke-demo-trade-01-CFO"})
        check(status == 202 and repeated["run_id"] == first["run_id"], "same idempotency payload returns same run")
        status, invalid = request(base, "/api/v1/companies/demo-trade-01/feed?role_id=CEO")
        check(status == 422 and invalid["error"]["code"] == "invalid_input", "unknown role rejected")
        status, invalid = request(base, "/api/v1/companies/demo-trade-01/analysis-runs", "POST",
                                  {"role_id": "CFO", "idempotency_key": "bad", "scope": "outside-company"})
        check(status == 422 and invalid["error"]["code"] == "invalid_input", "unknown request scope rejected by strict schema")
        status, feed = request(base, "/api/v1/companies/demo-trade-01/feed?role_id=CFO")
        check(status == 200 and len(feed["items"]) == 1, "repeat run creates no duplicate feed cards")

        insight_id = first["result_ids"][0]
        status, card = request(base, f"/api/v1/insights/{insight_id}")
        source_ids = {source["source_id"] for source in card.get("sources", [])}
        refs_resolve = bool(source_ids) and all(set(block["evidence_refs"]).issubset(source_ids) for block in card["blocks"])
        check(status == 200 and refs_resolve and all(s["synthetic"] and s["trust_status"] == "synthetic" for s in card["sources"]),
              "detail evidence refs resolve to synthetic sources")
        check(card["bank_position_status"] == "not_checked" and "bank_position" not in [b["kind"] for b in card["blocks"]],
              "bank position absence is explicit")

        stop(proc)
        processes.remove(proc)
        proc, base = start(python, demo_db, True)
        processes.append(proc)
        status, persisted = request(base, f"/api/v1/analysis-runs/{first['run_id']}")
        check(status == 200 and persisted == first, "run persists after uvicorn restart")
        status, persisted_card = request(base, f"/api/v1/insights/{insight_id}")
        check(status == 200 and persisted_card["insight_id"] == insight_id, "insight persists after uvicorn restart")
        print(f"SQLite: {demo_db}")
        print("UNTESTED fingerprint-mutation conflict: no supported public input mutates fingerprint components.")
        return 0
    finally:
        for proc in reversed(processes):
            stop(proc)


if __name__ == "__main__":
    raise SystemExit(main())
