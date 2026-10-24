import json
import io
import subprocess
import sys
import unittest
from unittest.mock import patch

from backend.analysis.pipeline import AnalysisInput, AnalysisPipeline, Evidence, ToolResult
from backend.model_clients import (
    ALLOWED_MODELS, AuthenticationFailure, BrokerFailure, BrokerModelClient,
    BROKER_TIMEOUT_SECONDS, BudgetExceeded, CorrectionUnsupported, InvalidModel, InvalidRequest,
    ModelTimeout, ProtocolFailure, TaskIdConflict, UnknownOutcome,
    _subprocess_transport,
)

CANDIDATE = {"kind": "observed_fact", "claim": "Synthetic fact", "evidence_refs": ["e1"],
             "exposure_status": "unknown", "bank_source_id": None}


class CaptureInput(io.BytesIO):
    def __init__(self):
        super().__init__()
        self.captured = bytearray()
    def write(self, value):
        self.captured.extend(value)
        return super().write(value)


class CountingInput(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.consumed = 0
    def read(self, size=-1):
        value = super().read(size)
        self.consumed += len(value)
        return value


def response(task="task-1", model="deepseek/deepseek-v4.1-flash", content=None, usage=None, status="ok", error=None):
    value = {"task_id": task, "model": model, "status": status}
    if status == "ok":
        value.update(content=json.dumps(CANDIDATE) if content is None else content, usage=usage)
    else:
        value["error"] = error
    return json.dumps(value)


class BrokerClientTests(unittest.TestCase):
    def test_exact_allowlist_and_default(self):
        self.assertEqual(len(ALLOWED_MODELS), 6)
        self.assertEqual(BrokerModelClient("task-1", transport=lambda _: "{} ").model,
                         "deepseek/deepseek-v4.1-flash")
        with self.assertRaises(InvalidModel):
            BrokerModelClient("task-1", model="https://example.invalid", transport=lambda _: "{}")
        with self.assertRaises(InvalidRequest):
            BrokerModelClient("task-1")

    def test_package_import_variants_share_exception_classes(self):
        sys.path.insert(0, "backend")
        try:
            import model_clients
        finally:
            sys.path.pop(0)
        import backend.model_clients as qualified
        self.assertIs(model_clients, qualified)
        self.assertIs(model_clients.ModelTimeout, qualified.ModelTimeout)

    def test_valid_candidate_and_usage_allowlist(self):
        calls = []
        client = BrokerModelClient("task-1", transport=lambda job: (calls.append(job.copy()) or
                                 response(usage={"prompt_tokens": 12, "total_tokens": 20})))
        self.assertEqual(client.generate({"untrusted_data_boundary": "data"}), CANDIDATE)
        self.assertEqual(set(calls[0]), {"task_id", "model", "prompt"})

    def test_candidate_is_validated_by_pipeline_not_client(self):
        from backend.analysis.pipeline import ModelTimeout
        evidence = Evidence("e1", "s1", "news", "Synthetic event.")
        data = AnalysisInput("synthetic-co", "CFO", "synthetic-event", (evidence,))
        class Tools:
            def fetch(self, request, limit): return ToolResult(())
        client = BrokerModelClient("task-1", transport=lambda _: response())
        result = AnalysisPipeline(client, Tools()).run(data)
        self.assertEqual(result.status, "validated")
        self.assertEqual(result.validated, CANDIDATE)

    def test_pipeline_maps_client_budget_auth_and_timeout_errors(self):
        evidence = Evidence("e1", "s1", "news", "Synthetic event.")
        data = AnalysisInput("synthetic-co", "CFO", "synthetic-event", (evidence,))
        class Tools:
            def fetch(self, request, limit): return ToolResult(())

        cases = (
            ("daily_budget_blocked", "stopped", "budget_exceeded"),
            ("upstream_unauthorized", "stopped", "authentication_failure"),
            ("upstream_timeout", "unknown_outcome", "timeout"),
        )
        for code, expected_status, expected_reason in cases:
            with self.subTest(code=code):
                client = BrokerModelClient(
                    "task-1", transport=lambda _, c=code: response(status="error", error=c))
                result = AnalysisPipeline(client, Tools()).run(data)
                self.assertEqual(result.status, expected_status)
                self.assertEqual(result.reasons, (expected_reason,))

    def test_malformed_and_oversize(self):
        with self.assertRaises(ProtocolFailure):
            BrokerModelClient("task-1", transport=lambda _: "not-json").generate({})
        with self.assertRaises(ProtocolFailure):
            BrokerModelClient("task-1", transport=lambda _: " " * 2_100_001).generate({})
        with self.assertRaises(ProtocolFailure):
            BrokerModelClient("task-1", transport=lambda _: response(content="plain prose")).generate({})
        bad_usage = response(usage={"secret": 1})
        with self.assertRaises(ProtocolFailure):
            BrokerModelClient("task-1", transport=lambda _: bad_usage).generate({})

    def test_errors_map_to_safe_exceptions(self):
        cases = [
            ("daily_attempt_limit", BudgetExceeded), ("upstream_unauthorized", AuthenticationFailure),
            ("invalid_model", InvalidModel), ("task_id_conflict", TaskIdConflict),
            ("upstream_error", BrokerFailure), ("upstream_timeout", ModelTimeout),
        ]
        for code, error_type in cases:
            with self.subTest(code=code):
                client = BrokerModelClient("task-1", transport=lambda _, c=code: response(status="error", error=c))
                with self.assertRaises(error_type):
                    client.generate({})

    def test_timeout_or_unknown_never_retries_same_object(self):
        for thrown in (ModelTimeout("safe"), RuntimeError("sensitive detail")):
            calls = []
            def transport(job):
                calls.append(job)
                raise thrown
            client = BrokerModelClient("task-1", transport=transport)
            with self.assertRaises((ModelTimeout, UnknownOutcome)):
                client.generate({"x": 1})
            with self.assertRaises(UnknownOutcome):
                client.generate({"x": 1})
            self.assertEqual(len(calls), 1)
            with self.assertRaises(TaskIdConflict):
                client.generate({"x": 2})

    def test_correction_and_prompt_bounds_fail_closed(self):
        calls = []
        client = BrokerModelClient("task-1", transport=lambda job: calls.append(job))
        with self.assertRaises(CorrectionUnsupported):
            client.generate({}, correction="please fix")
        with self.assertRaises(InvalidRequest):
            client.generate({"text": "x" * 12_100})
        self.assertEqual(calls, [])

    def test_live_transport_arguments_and_stdin_separation(self):
        class Proc:
            returncode = 0
            def __init__(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs
                self.stdin = CaptureInput()
                self.stdout, self.stderr = io.BytesIO(b"{}"), io.BytesIO(b"discarded diagnostic")
            def wait(self, timeout=None): return 0
            def terminate(self): pass
            def kill(self): pass
        made = []
        def factory(*args, **kwargs):
            made.append(Proc(*args, **kwargs))
            return made[-1]
        with patch("backend.model_clients.subprocess.Popen", side_effect=factory):
            _subprocess_transport({"task_id": "task-1", "model": "m", "prompt": "sensitive prompt"})
            args, kwargs = made[0].args, made[0].kwargs
            self.assertEqual(args[0], ("ssh", "model-broker", "python3", "/opt/alpha-broker/broker.py", "run"))
            self.assertNotIn("sensitive prompt", " ".join(args[0]))
            self.assertIn(b"sensitive prompt", made[0].stdin.captured)
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["stdin"], subprocess.PIPE)
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(kwargs["stderr"], subprocess.PIPE)

    def test_operator_broker_settings_are_data_and_reject_remote_shell_syntax(self):
        from backend.model_clients import _broker_command
        with patch.dict("os.environ", {"ALPHA_BROKER_HOST": "qa-broker", "ALPHA_BROKER_SCRIPT": "/srv/broker/run.py"}):
            self.assertEqual(_broker_command(), ("ssh", "qa-broker", "python3", "/srv/broker/run.py", "run"))
        for host, script in (("-oProxyCommand=x", "/srv/run.py"), ("host;id", "/srv/run.py"),
                             ("host", "/srv/run.py;id"), ("host", "/srv/../run.py"),
                             ("host", "/srv/$(id).py"), ("host", "relative.py")):
            with self.subTest(host=host, script=script):
                with patch.dict("os.environ", {"ALPHA_BROKER_HOST": host, "ALPHA_BROKER_SCRIPT": script}):
                    with patch("backend.model_clients.subprocess.Popen") as start:
                        with self.assertRaises(InvalidRequest):
                            _subprocess_transport({"task_id": "t", "model": "m", "prompt": "p"})
                        start.assert_not_called()

    def test_stdout_is_bounded_and_stderr_is_drained(self):
        class Proc:
            returncode = 0
            def __init__(self, *args, **kwargs):
                self.stdin = CaptureInput()
                self.stdout = CountingInput(b"x" * (2_100_001))
                self.stderr = CountingInput(b"diagnostic" * 200_000)
                self.terminated = False
            def wait(self, timeout=None): return 0
            def terminate(self): self.terminated = True
            def kill(self): pass
        proc = Proc()
        with self.assertRaises(ProtocolFailure):
            _subprocess_transport({"task_id": "t", "model": "m", "prompt": "p"}, _popen=lambda *a, **k: proc)
        self.assertTrue(proc.terminated)
        self.assertEqual(proc.stdout.consumed, 2_100_001)
        self.assertEqual(proc.stderr.consumed, len(b"diagnostic" * 200_000))

    def test_nonzero_is_unknown_and_timeout_cleans_child(self):
        class Proc:
            def __init__(self, timeout=False):
                self.returncode = 9
                self.stdin = CaptureInput()
                self.stdout, self.stderr = CountingInput(b""), CountingInput(b"canary-diagnostic")
                self.timeout, self.terminated, self.killed = timeout, False, False
            def wait(self, timeout=None):
                if self.timeout: raise subprocess.TimeoutExpired("fixed", timeout)
                return self.returncode
            def terminate(self): self.terminated = True
            def kill(self): self.killed = True
        failed = Proc()
        with self.assertRaises(UnknownOutcome):
            _subprocess_transport({"task_id": "t", "model": "m", "prompt": "p"}, _popen=lambda *a, **k: failed)
        timed = Proc(timeout=True)
        with self.assertRaises(ModelTimeout) as caught:
            _subprocess_transport({"task_id": "t", "model": "m", "prompt": "p"}, _popen=lambda *a, **k: timed)
        self.assertNotIn("canary", str(caught.exception))
        self.assertTrue(timed.terminated)

    def test_live_transport_timeout_is_explicit(self):
        # Covered by bounded transport timeout/cleanup test without launching SSH.
        self.assertEqual(BROKER_TIMEOUT_SECONDS, 100)


if __name__ == "__main__":
    unittest.main()
