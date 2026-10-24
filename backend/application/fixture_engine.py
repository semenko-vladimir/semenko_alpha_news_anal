"""Bounded synthetic analysis engine using the production offline chain.

This engine demonstrates orchestration only. Its injected transport is a fixed
local fixture and its output is not evidence of model quality.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from analysis.pipeline import (
    AnalysisInput,
    AnalysisPipeline,
    BudgetExceeded,
    ToolRequest,
    ToolResult,
)
from ingestion.source_bridge import normalize
from model_clients import BrokerModelClient
from sources.cbr_rss import SourceItem

ENGINE_VERSION = "fixture-engine-v1"
CORPUS_REVISION = "synthetic-corpus-v1"
COMPANIES = ("demo-trade-01", "demo-transport-01")
ROLES = ("CFO", "COO")
MAX_CORPUS_ITEMS = 2
MAX_CORPUS_TEXT = 3_000
MAX_RESULT_CARDS = 2


@dataclass(frozen=True)
class EngineResult:
    status: str
    cards: tuple[dict, ...]
    trace: tuple[dict, ...]
    error_code: str | None


class _EmptyTools:
    def fetch(self, request: ToolRequest, limit: int) -> ToolResult:
        # Synthetic corpus is bounded and complete for this demo. Never fetch.
        return ToolResult(())


def _synthetic_item(company_id: str) -> SourceItem:
    if company_id == "demo-trade-01":
        title = "Синтетический пример изменения спроса для торговли"
        body = "Тестовый сценарий: изменение спроса может затронуть торговые операции. Экспозиция не установлена."
    else:
        title = "Синтетический пример изменения спроса для транспорта"
        body = "Тестовый сценарий: изменение спроса может затронуть транспортные операции. Экспозиция не установлена."
    return SourceItem(
        source_id=f"fixture:market-scenario:{company_id}", title=title,
        canonical_url=None, guid=f"fixture-event:{company_id}",
        published_at_raw=None, published_at=None, body_text=body,
        content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        provenance="fixture", synthetic=True,
    )


def _fingerprint_item(item: SourceItem) -> str:
    payload = [item.source_id, item.title, item.guid, item.body_text,
               item.content_hash, item.provenance, item.synthetic]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


class FixtureAnalysisEngine:
    """No-argument deterministic orchestration fixture for two demo companies."""

    def snapshot(self, company_id: str, role_id: str) -> dict:
        self._check_scope(company_id, role_id)
        item = _synthetic_item(company_id)
        return {
            "engine_version": ENGINE_VERSION,
            "corpus_revision": CORPUS_REVISION,
            "role_id": role_id,
            "event_ids": [f"fixture:event:{company_id}"],
            "source_fingerprints": {item.source_id: _fingerprint_item(item)},
        }

    def run(self, company_id: str, role_id: str) -> EngineResult:
        try:
            self._check_scope(company_id, role_id)
            items = (_synthetic_item(company_id),)
            if len(items) > MAX_CORPUS_ITEMS or any(
                item.body_text is None or len(item.body_text) > MAX_CORPUS_TEXT for item in items
            ):
                return EngineResult("failed", (), ({"stage": "corpus", "status": "rejected"},), "input_limit")
            bridge = normalize(items)
            if bridge.errors or len(bridge.records) != 1 or bridge.records[0].evidence is None:
                return EngineResult("failed", (), ({"stage": "bridge", "status": "rejected"},), "source_invalid")
            record = bridge.records[0]
            evidence = record.evidence
            assert evidence is not None
            if record.synthetic is not True or not record.source_id or not record.source_id.startswith("fixture:"):
                return EngineResult("failed", (), ({"stage": "bridge", "status": "rejected"},), "source_invalid")

            claim = self._claim(company_id, role_id)
            response_transport = _FixtureTransport()
            model = BrokerModelClient(f"engine-{company_id}-{role_id}", transport=response_transport)
            pipeline = AnalysisPipeline(model, _EmptyTools())
            result = pipeline.run(AnalysisInput(
                company_id, role_id, record.event_id, (evidence,),
                ("synthetic fixture only; company exposure unknown",),
            ))
            trace = tuple(self._safe_trace(item) for item in result.trace)
            if result.status != "validated" or result.validated is None:
                code = result.unknown or (result.reasons[0] if result.reasons else "analysis_failed")
                status = "unknown_outcome" if result.status == "unknown_outcome" else "failed"
                return EngineResult(status, (), trace, self._safe_code(code))
            candidate = result.validated
            if candidate.get("kind") not in {"observed_fact", "ai_inference"}:
                return EngineResult("failed", (), trace + ({"stage": "mapping", "status": "rejected"},), "output_invalid")
            evidence_to_source = {evidence.evidence_id: record.source_id}
            refs = candidate.get("evidence_refs", [])
            if any(ref not in evidence_to_source for ref in refs):
                return EngineResult("failed", (), trace + ({"stage": "mapping", "status": "rejected"},), "provenance_unresolved")
            refs = list(dict.fromkeys(evidence_to_source[ref] for ref in refs))
            card = {
                "event_id": f"fixture:event:{company_id}",
                "headline": self._headline(company_id, role_id),
                "summary": "Синтетический сценарий для обсуждения; фактическая экспозиция и эффект неизвестны.",
                "bank_position_status": "not_checked",
                "exposure_status": "conditional" if candidate["exposure_status"] == "conditional" else "unknown",
                "blocks": [{
                    "block_id": "fixture-inference-1",
                    "kind": "ai_inference",
                    "text": candidate["claim"],
                    "evidence_refs": refs,
                    "assumptions": ["Возможная связь компании с событием является сценарием."],
                    "unknowns": ["Реальная экспозиция и масштаб эффекта не проверены."],
                }],
                "sources": [{
                    "source_id": record.source_id, "kind": "synthetic_fixture",
                    "title": record.title, "uri": None, "published_at": None,
                    "retrieved_at": None, "document_date": None,
                    "locator": "synthetic fixture; no external document",
                    "synthetic": True, "trust_status": "synthetic",
                }],
                "unknowns": ["Экспозиция и реальные бизнес-показатели не предоставлены.",
                             "Позиция Альфа-Банка не проверялась."],
            }
            return EngineResult("succeeded", (card,), trace, None)
        except (TypeError, ValueError, KeyError, AttributeError):
            return EngineResult("failed", (), ({"stage": "engine", "status": "rejected"},), "input_invalid")

    @staticmethod
    def _check_scope(company_id: str, role_id: str) -> None:
        if company_id not in COMPANIES or role_id not in ROLES:
            raise ValueError("input_invalid")

    @staticmethod
    def _claim(company_id: str, role_id: str) -> str:
        return FixtureAnalysisEngine._headline(company_id, role_id) + "; подтвердите фактическую экспозицию до выводов."

    @staticmethod
    def _headline(company_id: str, role_id: str) -> str:
        if role_id == "CFO":
            return "Сценарий денежного потока требует проверки" if company_id == "demo-trade-01" else "Сценарий затрат на маршруты требует проверки"
        return "Операционная устойчивость зависит от экспозиции" if company_id == "demo-trade-01" else "Устойчивость маршрутов требует фактических данных"

    @staticmethod
    def _safe_trace(item: Mapping[str, Any]) -> dict:
        allowed = {"stage", "status", "attempt", "source_ids"}
        return {key: item[key] for key in allowed if key in item}

    @staticmethod
    def _safe_code(code: str) -> str:
        allowed = {"budget_exceeded", "authentication_failure", "timeout", "unknown_outcome",
                   "tool_failure", "tool_result_invalid", "model_failure", "model_failure",
                   "input_invalid", "input_size_limit", "schema_version_invalid"}
        return code if code in allowed else "analysis_failed"


class _FixtureTransport:
    """Local deterministic transport implementing the production client protocol."""
    def __call__(self, job: Mapping[str, str]) -> str:
        prompt = json.loads(job["prompt"])
        refs = [entry["evidence_id"] for entry in prompt.get("evidence", [])]
        candidate = {
            "kind": "ai_inference",
            "claim": FixtureAnalysisEngine._claim(prompt["company_id"], prompt["role_id"]),
            "evidence_refs": refs,
            "exposure_status": "conditional",
            "bank_source_id": None,
        }
        return json.dumps({"task_id": job["task_id"], "model": job["model"],
                           "status": "ok", "content": json.dumps(candidate, ensure_ascii=False)},
                          ensure_ascii=False)
