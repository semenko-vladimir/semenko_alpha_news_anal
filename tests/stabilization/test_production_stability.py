"""Offline stabilization checks through production pipeline and fixture engine."""
from __future__ import annotations

import json
import statistics
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from analysis.pipeline import AnalysisInput, AnalysisPipeline, Evidence, ToolResult  # noqa: E402
from application.fixture_engine import FixtureAnalysisEngine  # noqa: E402
from model_clients import BrokerModelClient  # noqa: E402


def candidate(refs, *, kind="ai_inference", exposure="unknown", bank=None):
    return {"kind": kind, "claim": "Synthetic scenario; validate before action.",
            "evidence_refs": refs, "exposure_status": exposure, "bank_source_id": bank}


class EmptyTools:
    def fetch(self, request, limit):
        return ToolResult(())


class LocalTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def __call__(self, job):
        self.calls += 1
        return json.dumps({"task_id": job["task_id"], "model": job["model"],
                           "status": "ok", "content": json.dumps(self.payload)})


class ProductionStability(unittest.TestCase):
    def setUp(self):
        (ROOT / ".tmp/stability").mkdir(parents=True, exist_ok=True)

    def run_case(self, role, payload, ev=None):
        evidence = ev or Evidence("ev-1", "news-1", "news", "Synthetic demand report")
        transport = LocalTransport(payload([evidence.evidence_id]) if callable(payload) else payload)
        result = AnalysisPipeline(BrokerModelClient("stabilize-local", transport=transport), EmptyTools()).run(
            AnalysisInput("company-fixture", role, "event-fixture", (evidence,), ("synthetic",)))
        return result, transport.calls

    def test_fixed_contract_smoke_acceptance_and_adversarial_rejection(self):
        corpus = [
            ("CFO", candidate, "validated"),
            ("COO", candidate, "validated"),
            ("CFO", lambda refs: candidate(refs, kind="observed_fact"), "validated"),
            ("CFO", lambda refs: candidate(["foreign-source"]), "unknown_outcome"),
            ("COO", lambda refs: candidate(refs, exposure="confirmed"), "unknown_outcome"),
            ("CFO", lambda refs: candidate(refs, kind="bank_position", bank="news-1"), "unknown_outcome"),
        ]
        observed = []
        for role, output, expected in corpus:
            with self.subTest(role=role, expected=expected):
                result, calls = self.run_case(role, output)
                self.assertEqual(result.status, expected)
                self.assertEqual(calls, 1)
                observed.append({"role": role, "expected": expected, "actual": result.status,
                                 "transport_calls": calls})
        self.assertEqual(sum(x["actual"] == "validated" for x in observed), 3)
        self.assertEqual(sum(x["actual"] == "unknown_outcome" for x in observed), 3)
        (ROOT / ".tmp/stability/quality-results.json").write_text(
            json.dumps({"dataset": "fixed synthetic contract smoke cases; not held-out evaluation",
                        "cases": observed},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_fixture_engine_under_local_concurrency_and_replay(self):
        engine = FixtureAnalysisEngine()
        jobs = [(company, role) for company in ("demo-trade-01", "demo-transport-01")
                for role in ("CFO", "COO")] * 5
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda item: engine.run(*item), jobs))
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.assertEqual(len(results), 20)
        self.assertTrue(all(r.status == "succeeded" and len(r.cards) == 1 for r in results))
        self.assertTrue(all(r.cards[0]["sources"][0]["synthetic"] is True for r in results))
        self.assertTrue(all("Экспозиция" in r.cards[0]["unknowns"][0] for r in results))
        report = {"case_count": len(results), "workers": 4, "elapsed_ms": round(elapsed_ms, 3),
                  "mean_ms_per_run": round(elapsed_ms / len(results), 3),
                  "statuses": {s: sum(r.status == s for r in results) for s in {r.status for r in results}},
                  "synthetic_sources": sum(r.cards[0]["sources"][0]["synthetic"] for r in results)}
        (ROOT / ".tmp/stability/local-load.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.assertGreater(report["elapsed_ms"], 0)

    def test_fixture_replay_is_stable(self):
        engine = FixtureAnalysisEngine()
        first = engine.run("demo-trade-01", "CFO")
        second = engine.run("demo-trade-01", "CFO")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
