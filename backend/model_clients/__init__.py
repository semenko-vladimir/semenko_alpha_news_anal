"""Fail-closed client for the fixed Alpha hackathon SSH model broker."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from typing import Any, Callable, Mapping

# Keep pipeline exception identity when this package is imported either as
# backend.model_clients or model_clients (backend/ on sys.path).
import importlib
import sys
if "backend.analysis.pipeline" in sys.modules:
    _pipeline = sys.modules["backend.analysis.pipeline"]
elif "analysis.pipeline" in sys.modules:
    _pipeline = sys.modules["analysis.pipeline"]
elif __package__.startswith("backend."):
    _pipeline = importlib.import_module("backend.analysis.pipeline")
else:
    _pipeline = importlib.import_module("analysis.pipeline")
sys.modules.setdefault("analysis.pipeline", _pipeline)
sys.modules.setdefault("backend.analysis.pipeline", _pipeline)

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
ALLOWED_MODELS = frozenset({
    "google/gemma-4-26b-a4b-it", "qwen/qwen3.6-35b-a3b", "z-ai/glm-5.3-flash",
    DEFAULT_MODEL, "qwen/qwen3-vl-30b-a3b-instruct", "moonshotai/kimi-k2.6",
})
MAX_PROMPT_CHARS = 12_000
MAX_STDOUT_BYTES = 2_100_000
BROKER_TIMEOUT_SECONDS = 100
BROKER_COMMAND = ("ssh", "model-broker", "python3", "/opt/alpha-broker/broker.py", "run")


def _broker_command():
    # Only these named non-secret operator settings; no environment enumeration.
    host = os.getenv("ALPHA_BROKER_HOST", BROKER_COMMAND[1])
    script = os.getenv("ALPHA_BROKER_SCRIPT", BROKER_COMMAND[3])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}", host):
        raise InvalidRequest("Invalid broker host")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", script) or ".." in script.split("/"):
        raise InvalidRequest("Invalid broker script path")
    return ("ssh", host, "python3", script, "run")
USAGE_KEYS = frozenset({"prompt_tokens", "completion_tokens", "total_tokens",
                        "reasoning_tokens", "cached_tokens", "prompt_cached_tokens",
                        "completion_reasoning_tokens", "completion_cached_tokens"})


class ClientError(Exception):
    """Safe, non-secret client failure."""


class InvalidModel(ClientError): pass
class InvalidRequest(ClientError): pass
class ProtocolFailure(ClientError): pass
class BrokerFailure(ClientError): pass
class BudgetExceeded(ClientError, _pipeline.BudgetExceeded): pass
class AuthenticationFailure(ClientError, _pipeline.AuthenticationFailure): pass
class ModelTimeout(ClientError, _pipeline.ModelTimeout): pass
class UnknownOutcome(ClientError): pass
class TaskIdConflict(ClientError): pass
class CorrectionUnsupported(ClientError): pass


def _subprocess_transport(job: Mapping[str, str], *, _popen=None) -> str:
    """Explicit live transport. Never place job data in process arguments."""
    encoded = json.dumps(dict(job), ensure_ascii=False, separators=(",", ":"))
    popen = _popen or subprocess.Popen
    deadline = time.monotonic() + BROKER_TIMEOUT_SECONDS
    try:
        proc = popen(_broker_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, shell=False)
    except OSError as exc:
        raise UnknownOutcome("Broker transport could not confirm a result") from exc

    stdout_data = bytearray()
    overflow = threading.Event()
    reader_errors: list[BaseException] = []
    input_errors: list[BaseException] = []

    def read_stdout() -> None:
        try:
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    return
                room = MAX_STDOUT_BYTES - len(stdout_data)
                if len(chunk) > room:
                    if room > 0:
                        stdout_data.extend(chunk[:room])
                    overflow.set()
                    # The child is ours; stop it as soon as overflow is known,
                    # while continuing to drain both pipes during cleanup.
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                elif not overflow.is_set():
                    stdout_data.extend(chunk)
        except BaseException as exc:
            reader_errors.append(exc)

    def drain_stderr() -> None:
        try:
            while proc.stderr.read(64 * 1024):
                pass
        except BaseException as exc:
            reader_errors.append(exc)

    def write_stdin() -> None:
        try:
            proc.stdin.write(encoded.encode("utf-8"))
        except (BrokenPipeError, OSError) as exc:
            input_errors.append(exc)
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    def stop_child() -> None:
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass

    readers = [threading.Thread(target=read_stdout, daemon=True),
               threading.Thread(target=drain_stderr, daemon=True),
               threading.Thread(target=write_stdin, daemon=True)]
    for reader in readers:
        reader.start()
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or proc.wait(timeout=max(0, remaining)) is None:
            raise subprocess.TimeoutExpired(BROKER_COMMAND, BROKER_TIMEOUT_SECONDS)
        for reader in readers:
            reader.join(max(0, deadline - time.monotonic()))
        if any(reader.is_alive() for reader in readers):
            raise subprocess.TimeoutExpired(BROKER_COMMAND, BROKER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        stop_child()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
        for reader in readers:
            reader.join(1)
        raise ModelTimeout("Broker transport timed out; outcome may be unknown") from None
    except OSError as exc:
        stop_child()
        raise UnknownOutcome("Broker transport could not confirm a result") from exc
    finally:
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
    # stderr and reader details are deliberately never surfaced.
    if overflow.is_set():
        raise ProtocolFailure("Broker response exceeds the client limit")
    if reader_errors or input_errors or proc.returncode != 0:
        raise UnknownOutcome("Broker exited without a confirmed result")
    try:
        return bytes(stdout_data).decode("utf-8")
    except UnicodeDecodeError:
        raise ProtocolFailure("Broker response is not valid UTF-8") from None


class BrokerModelClient:
    """Injected transport by default; live SSH is opt-in via live()."""
    def __init__(self, task_id: str, *, model: str = DEFAULT_MODEL,
                 transport: Callable[[Mapping[str, str]], str] | None = None):
        if not isinstance(task_id, str) or not task_id or len(task_id) > 100 or any(
                ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-" for ch in task_id):
            raise InvalidRequest("A stable project task_id is required")
        if model not in ALLOWED_MODELS:
            raise InvalidModel("Model is not in the fixed organizer allowlist")
        if transport is None:
            raise InvalidRequest("Inject a transport or explicitly use BrokerModelClient.live()")
        self.task_id, self.model, self._transport = task_id, model, transport
        self._lock = threading.Lock()
        self._fingerprint: str | None = None
        self._uncertain = False

    @classmethod
    def live(cls, task_id: str, *, model: str = DEFAULT_MODEL) -> "BrokerModelClient":
        """Explicitly enable the fixed SSH invocation; does not run it here."""
        return cls(task_id, model=model, transport=_subprocess_transport)

    def generate(self, context: Mapping[str, Any], correction: str | None = None) -> Mapping[str, Any]:
        if correction is not None:
            # Broker idempotency binds a task ID to one exact prompt. A correction
            # needs a separately assigned task ID; this client never invents one.
            raise CorrectionUnsupported("Correction requires a separately assigned task_id")
        if not isinstance(context, Mapping):
            raise InvalidRequest("Context must be an object")
        prompt = json.dumps(dict(context), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(prompt) > MAX_PROMPT_CHARS:
            raise InvalidRequest("Prompt exceeds broker limit")
        import hashlib
        fingerprint = hashlib.sha256((self.model + "\0" + prompt).encode("utf-8")).hexdigest()
        with self._lock:
            if self._fingerprint is not None and self._fingerprint != fingerprint:
                raise TaskIdConflict("Task ID is already bound to a different prompt")
            if self._uncertain:
                raise UnknownOutcome("Prior outcome is unresolved; automatic retry is forbidden")
            self._fingerprint = fingerprint
            job = {"task_id": self.task_id, "model": self.model, "prompt": prompt}
            try:
                raw = self._transport(job)
            except (ModelTimeout, UnknownOutcome):
                self._uncertain = True
                raise
            except Exception as exc:
                # Injected transport failures may occur after transmission.
                self._uncertain = True
                raise UnknownOutcome("Broker result could not be confirmed") from exc
            return self._parse_result(raw)

    def _parse_result(self, raw: str) -> Mapping[str, Any]:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_STDOUT_BYTES:
            raise ProtocolFailure("Broker response exceeds the client limit")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ProtocolFailure("Broker returned malformed JSON") from exc
        if not isinstance(result, dict) or result.get("task_id") != self.task_id or result.get("model") != self.model:
            raise ProtocolFailure("Broker response identity is invalid")
        if result.get("status") != "ok":
            code = result.get("error")
            if code in {"daily_budget_blocked", "daily_attempt_limit", "budget_exceeded"}:
                raise BudgetExceeded("Broker budget limit reached")
            if code in {"credential_missing", "credential_invalid", "upstream_unauthorized", "upstream_forbidden"}:
                raise AuthenticationFailure("Broker authentication failed")
            if code == "upstream_timeout" or code == "previous_request_unresolved":
                self._uncertain = True
                raise ModelTimeout("Broker could not confirm upstream completion")
            if code == "task_id_conflict":
                raise TaskIdConflict("Broker task ID fingerprint conflict")
            if code == "invalid_model":
                raise InvalidModel("Broker rejected the model")
            raise BrokerFailure("Broker returned a safe error status")
        content = result.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_STDOUT_BYTES:
            raise ProtocolFailure("Broker content is invalid or too large")
        usage = result.get("usage")
        if usage is not None:
            if not isinstance(usage, dict) or any(k not in USAGE_KEYS for k in usage) or any(
                    isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in usage.values()):
                raise ProtocolFailure("Broker usage is invalid")
        try:
            candidate = json.loads(content)
        except (ValueError, UnicodeError) as exc:
            raise ProtocolFailure("Model content is not a JSON object") from exc
        if not isinstance(candidate, dict):
            raise ProtocolFailure("Model content is not a JSON object")
        # This is an untrusted candidate only; pipeline remains responsible for validation.
        return candidate


ModelClient = BrokerModelClient

# Avoid loading a second copy of this module (and its typed exceptions) when
# callers use the historical top-level package path with backend/ on sys.path.
sys.modules.setdefault("model_clients", sys.modules[__name__])
sys.modules.setdefault("backend.model_clients", sys.modules[__name__])
