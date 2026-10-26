import io
import contextlib
import json
import os
import sqlite3
import socket
import tempfile
import threading
import unittest
import urllib.error
import warnings
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import broker


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.value


class FakeOpener:
    def __init__(self, response=None, error=None, redirect=False):
        self.response, self.error, self.redirect = response, error, redirect
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        self.request = request
        self.timeout = timeout
        if self.redirect:
            raise urllib.error.HTTPError("https://other.invalid", 302, "redirect", {}, None)
        if self.error:
            raise self.error
        return Response(self.response)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = self.root / "upstream.key"
        self.key.write_text("canary-secret", encoding="utf-8")
        self.db = self.root / "state" / "jobs.sqlite3"
        self.job = {"task_id": "case-1", "model": "google/gemma-4-26b-a4b-it", "prompt": "hello"}

    def tearDown(self):
        self.temp.cleanup()

    def run_with(self, opener, job=None, key_path=None, db_path=None):
        return broker.run_job(job or self.job, key_path=key_path or self.key,
                              opener=opener, db_path=db_path or self.db)

    def test_success_usage_redaction_and_idempotency(self):
        upstream = FakeOpener(json.dumps({"choices": [{"message": {"content": "answer canary-secret"}}],
                                         "usage": {"total_tokens": 7}}).encode())
        result = self.run_with(upstream)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["content"], "answer [REDACTED]")
        self.assertEqual(result["usage"]["total_tokens"], 7)
        self.assertEqual(upstream.request.full_url, broker.BASE_URL + "/chat/completions")
        self.assertEqual(upstream.timeout, broker.UPSTREAM_TIMEOUT_SECONDS)
        self.assertEqual(upstream.request.get_header("Authorization"), "Bearer canary-secret")
        retry = self.run_with(FakeOpener())
        self.assertEqual(retry, result)

    def test_usage_canary_is_filtered_from_result_and_state(self):
        upstream = FakeOpener(json.dumps({
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2,
                      "total_tokens": 5, "prompt_tokens_details": {"cached_tokens": 1},
                      "completion_tokens_details": {"reasoning_tokens": 0},
                      "secret_echo": "canary-secret", "nested": {"value": "canary-secret"},
                      "bad_count": True, "cached_tokens": False, "string_count": "9"},
        }).encode())
        result = self.run_with(upstream)
        self.assertEqual(result["usage"], {"prompt_tokens": 3, "completion_tokens": 2,
                                           "total_tokens": 5, "prompt_cached_tokens": 1,
                                           "completion_reasoning_tokens": 0})
        self.assertNotIn("canary-secret", json.dumps(result))
        with closing(sqlite3.connect(self.db)) as db, db:
            persisted = db.execute("SELECT result FROM jobs WHERE task_id=?", (self.job["task_id"],)).fetchone()[0]
        self.assertNotIn("canary-secret", persisted)

    def test_existing_reserved_record_never_calls_upstream(self):
        broker.reserve(self.job["task_id"], self.job["model"], self.job["prompt"], db_path=self.db)
        upstream = FakeOpener(json.dumps({"choices": [{"message": {"content": "duplicate"}}]}).encode())
        result = self.run_with(upstream)
        self.assertEqual(upstream.calls, 0)
        self.assertEqual(result["error"], "previous_request_unresolved")

    def test_task_id_prompt_or_model_conflict_fails_before_network(self):
        first = FakeOpener(json.dumps({"choices": [{"message": {"content": "done"}}]}).encode())
        self.assertEqual(self.run_with(first)["status"], "ok")
        for changed in [dict(self.job, prompt="different prompt"),
                        dict(self.job, model="qwen/qwen3.6-35b-a3b")]:
            upstream = FakeOpener()
            result = self.run_with(upstream, job=changed)
            self.assertEqual(result["error"], "task_id_conflict")
            self.assertEqual(upstream.calls, 0)

    def test_concurrent_reservation_is_unique(self):
        barrier = threading.Barrier(2)
        outcomes = []
        failures = []

        def reserve_once():
            try:
                barrier.wait()
                outcomes.append(broker.reserve("concurrent", self.job["model"], self.job["prompt"], db_path=self.db)[0])
            except Exception as exc:
                failures.append(exc)

        workers = [threading.Thread(target=reserve_once) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(failures, [])
        self.assertCountEqual(outcomes, ["new", "pending"])

    def test_old_schema_migrates_without_releasing_reserved_job(self):
        self.db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE jobs (task_id TEXT PRIMARY KEY, day TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL, result TEXT)")
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?)", (self.job["task_id"], broker.budget_day(), self.job["model"], "reserved", None))
        upstream = FakeOpener()
        result = self.run_with(upstream)
        self.assertEqual(result["error"], "previous_request_unresolved")
        self.assertEqual(upstream.calls, 0)
        with closing(sqlite3.connect(self.db)) as db, db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            row = db.execute("SELECT status FROM jobs WHERE task_id=?", (self.job["task_id"],)).fetchone()
        self.assertIn("fingerprint", columns)
        self.assertEqual(row[0], "reserved")

    def test_concurrent_old_schema_migration_preserves_reservations(self):
        self.db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE jobs (task_id TEXT PRIMARY KEY, day TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL, result TEXT)")
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?)", ("legacy", broker.budget_day(), self.job["model"], "reserved", None))
        barrier = threading.Barrier(2)
        outcomes, failures = [], []

        def reserve_once(task):
            try:
                barrier.wait()
                outcomes.append(broker.reserve(task, self.job["model"], self.job["prompt"], db_path=self.db)[0])
            except Exception as exc:
                failures.append(exc)

        workers = [threading.Thread(target=reserve_once, args=(task,)) for task in ("legacy", "new")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(failures, [])
        self.assertCountEqual(outcomes, ["pending", "new"])
        with closing(sqlite3.connect(self.db)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            rows = db.execute("SELECT task_id,status FROM jobs ORDER BY task_id").fetchall()
        self.assertIn("fingerprint", columns)
        self.assertEqual(rows, [("legacy", "reserved"), ("new", "reserved")])

    def test_concurrent_distinct_ids_share_daily_quota(self):
        barrier = threading.Barrier(broker.MAX_DAILY + 1)
        outcomes, failures = [], []

        def reserve_once(index):
            try:
                barrier.wait()
                outcomes.append(broker.reserve(f"quota-{index}", self.job["model"], self.job["prompt"], db_path=self.db)[0])
            except broker.BrokerError as exc:
                failures.append(exc.code)
            except Exception as exc:
                failures.append(type(exc).__name__)

        workers = [threading.Thread(target=reserve_once, args=(index,)) for index in range(broker.MAX_DAILY + 1)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertCountEqual(outcomes, ["new"] * broker.MAX_DAILY)
        self.assertEqual(failures, ["daily_attempt_limit"])
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], broker.MAX_DAILY)

    def test_configure_fails_closed_on_getpass_warning(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
             patch.object(broker, "CONFIG", self.root / "config"), \
             patch.object(broker, "KEY_FILE", self.root / "config" / "upstream.key"), \
             patch.object(broker.sys.stdin, "isatty", return_value=True), \
             patch.object(broker.sys.stderr, "isatty", return_value=True), \
             patch.object(broker.getpass, "getpass", side_effect=lambda *_: warnings.warn("echo", broker.getpass.GetPassWarning)):
            with self.assertRaises(broker.BrokerError) as exc:
                broker.configure()
        self.assertEqual(exc.exception.code, "secure_input_unavailable")
        self.assertFalse((self.root / "config" / "upstream.key").exists())
        self.assertNotIn("canary-secret", stdout.getvalue() + stderr.getvalue())

    def test_configure_fake_key_is_atomic_private_and_never_printed(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        fake_key = "fake-config-canary"
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
             patch.object(broker, "CONFIG", self.root / "config"), \
             patch.object(broker, "KEY_FILE", self.root / "config" / "upstream.key"), \
             patch.object(broker.sys.stdin, "isatty", return_value=True), \
             patch.object(broker.sys.stderr, "isatty", return_value=True), \
             patch.object(broker.getpass, "getpass", return_value=fake_key):
            broker.configure()
        target = self.root / "config" / "upstream.key"
        self.assertEqual(target.read_text(encoding="utf-8"), fake_key)
        if os.name == "posix":
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(fake_key, stdout.getvalue() + stderr.getvalue())

    def test_validation_and_missing_key_before_network(self):
        op = FakeOpener()
        missing = self.run_with(op, key_path=self.root / "absent")
        self.assertEqual(missing["error"], "credential_missing")
        self.assertEqual(op.calls, 0)
        for job in [dict(self.job, extra="x"), dict(self.job, model="evil"),
                    dict(self.job, prompt="x" * (broker.MAX_PROMPT + 1))]:
            self.assertEqual(self.run_with(op, job=job)["status"], "error")
        self.assertEqual(op.calls, 0)

    def test_prompt_and_response_transport_bounds(self):
        op = FakeOpener()
        too_long = dict(self.job, prompt="x" * (broker.MAX_PROMPT + 1))
        rejected = self.run_with(op, job=too_long)
        self.assertEqual(rejected["error"], "prompt_too_long")
        self.assertEqual(op.calls, 0)

        oversized = FakeOpener(b"{" + b" " * broker.MAX_RESPONSE)
        rejected = self.run_with(oversized, job=dict(self.job, task_id="oversized-response"))
        self.assertEqual(rejected["error"], "response_too_large")
        self.assertEqual(oversized.calls, 1)
        self.assertEqual(len(oversized.response), broker.MAX_RESPONSE + 1)
    def test_malformed_and_http_errors_do_not_retry_or_leak(self):
        cases = [(FakeOpener(b"not json"), "malformed_upstream_response"),
                 (FakeOpener(error=urllib.error.HTTPError("x", 401, "canary-secret", {}, io.BytesIO(b"canary-secret"))), "upstream_unauthorized"),
                 (FakeOpener(redirect=True), "upstream_error")]
        for index, (fake, expected) in enumerate(cases):
            result = self.run_with(fake, job=dict(self.job, task_id=f"error-{index}"))
            self.assertEqual(result["error"], expected)
            self.assertNotIn("canary-secret", json.dumps(result))
            self.assertEqual(fake.calls, 1)

    def test_429_stops_all_new_tasks_for_day(self):
        limited = FakeOpener(error=urllib.error.HTTPError("x", 429, "limited", {}, io.BytesIO(b"")))
        self.assertEqual(self.run_with(limited)["error"], "upstream_limited")
        other = FakeOpener()
        self.assertEqual(self.run_with(other, job=dict(self.job, task_id="another"))["error"], "daily_budget_blocked")
        self.assertEqual(other.calls, 0)

    def test_budget_message_is_hidden_and_blocks_day(self):
        failure = urllib.error.HTTPError("x", 400, "failure", {}, io.BytesIO(b"Budget has been exceeded canary-secret"))
        result = self.run_with(FakeOpener(error=failure))
        self.assertEqual(result["error"], "budget_exceeded")
        self.assertNotIn("canary-secret", json.dumps(result))

    def test_timeout_is_safe_and_task_is_not_retried(self):
        fake = FakeOpener(error=urllib.error.URLError(socket.timeout("canary-secret")))
        first = self.run_with(fake)
        second_opener = FakeOpener()
        second = self.run_with(second_opener)
        self.assertEqual(first["error"], "upstream_timeout")
        self.assertGreaterEqual(first["duration_seconds"], 0)
        self.assertNotIn("canary-secret", json.dumps(first))
        self.assertEqual(second, first)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(second_opener.calls, 0)

    def test_error_result_is_idempotent_and_not_sent_again(self):
        failure = urllib.error.HTTPError("x", 401, "denied", {}, io.BytesIO(b""))
        first_opener = FakeOpener(error=failure)
        first = self.run_with(first_opener)
        second_opener = FakeOpener()
        second = self.run_with(second_opener)
        self.assertEqual(first, second)
        self.assertEqual(first["error"], "upstream_unauthorized")
        self.assertEqual(first_opener.calls, 1)
        self.assertEqual(second_opener.calls, 0)

    def test_error_result_is_idempotent_and_not_sent_again(self):
        failure = urllib.error.HTTPError("x", 401, "denied", {}, io.BytesIO(b""))
        first_opener = FakeOpener(error=failure)
        first = self.run_with(first_opener)
        second_opener = FakeOpener()
        second = self.run_with(second_opener)
        self.assertEqual(first, second)
        self.assertEqual(first["error"], "upstream_unauthorized")
        self.assertEqual(first_opener.calls, 1)
        self.assertEqual(second_opener.calls, 0)

    def test_daily_limit_four_unique_attempts(self):
        for n in range(4):
            op = FakeOpener(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())
            job = dict(self.job, task_id=f"case-{n}")
            self.assertEqual(self.run_with(op, job=job)["status"], "ok")
        op = FakeOpener()
        result = self.run_with(op, job=dict(self.job, task_id="case-5"))
        self.assertEqual(result["error"], "daily_attempt_limit")
        self.assertEqual(op.calls, 0)

    def test_state_permissions_and_secret_absent(self):
        self.run_with(FakeOpener(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()))
        if os.name == "posix":
            self.assertEqual(self.db.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        for path in self.db.parent.iterdir():
            self.assertNotIn(b"canary-secret", path.read_bytes())

    def test_configure_requires_tty_without_prompting(self):
        with patch.object(broker.sys.stdin, "isatty", return_value=False), \
             patch.object(broker.sys.stderr, "isatty", return_value=False):
            with self.assertRaises(broker.BrokerError) as exc:
                broker.configure()
        self.assertEqual(exc.exception.code, "tty_required")

    def test_deepseek_uses_compact_output_without_increasing_limits(self):
        upstream = FakeOpener(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())
        result = self.run_with(upstream, job=dict(self.job, model="deepseek/deepseek-v4.1-flash"))
        self.assertEqual(result["status"], "ok")
        payload = json.loads(upstream.request.data)
        self.assertEqual(payload.get("reasoning_effort"), "none")
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(broker.MAX_DAILY, 4)
        other = FakeOpener(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())
        self.run_with(other, job=dict(self.job, task_id="other-model"))
        self.assertNotIn("reasoning_effort", json.loads(other.request.data))

    def test_empty_content_is_completed_error_with_usage_and_no_retry(self):
        upstream = FakeOpener(json.dumps({"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 384,
                      "completion_tokens_details": {"reasoning_tokens": 384}}}).encode())
        first = self.run_with(upstream)
        self.assertEqual(first["status"], "error")
        self.assertEqual(first["error"], "empty_model_content")
        self.assertEqual(first["usage"]["completion_reasoning_tokens"], 384)
        self.assertEqual(first["finish_reason"], "length")
        repeated = FakeOpener()
        self.assertEqual(self.run_with(repeated), first)
        self.assertEqual(repeated.calls, 0)

    def test_status_is_aggregate_read_only_and_does_not_read_key(self):
        broker.reserve(self.job['task_id'], self.job['model'], self.job['prompt'], db_path=self.db)
        before = self.db.read_bytes()
        output = io.StringIO()
        with patch.object(broker, 'DB', self.db), patch.object(broker, 'KEY_FILE', self.key), \
             patch.object(Path, 'read_text', side_effect=AssertionError('status must not read credential contents')), \
             contextlib.redirect_stdout(output):
            broker.status()
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['used_attempts'], 1)
        self.assertEqual(result['remaining_attempts'], 3)
        self.assertEqual(result['max_output_tokens'], 512)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertNotIn('canary-secret', output.getvalue())


if __name__ == "__main__":
    unittest.main()
