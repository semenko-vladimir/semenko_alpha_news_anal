"""Synthetic fixtures exercise the same builder/SQL retrieval as local production."""

import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import uuid
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from bank_corpus import BankRepository, CorpusError, build_index

TEMP_ROOT = Path(__file__).resolve().parents[1] / ".tmp/tests/bank"


class BankCorpusTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        # Python 3.14 TemporaryDirectory mode=0700 conflicts with the Windows
        # restricted-token runner; inherit the workspace ACL with normal mkdir.
        self.root = TEMP_ROOT / ("test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(self.cleanup_fixture)
        self.index = self.root / "bank.sqlite3"
        self.manifest_path = self.root / "manifest.json"
        self.docs = []
        for doc_id, day, historical, bodies in [
            ("CURRENT", "2026-09-11", False, ["Ставка инфляция топливо. " * 100, "Уникальнаястраница кредит."]),
            ("OLD", "2021-04-15", False, ["Сталь историческиймаркер инфляция."]),
            ("UNDATED", None, False, ["Золото недатированныймаркер."]),
        ]:
            (self.root / f"{doc_id}.md").write_text("\n".join(f"## Страница {i}\n{body}\n" for i, body in enumerate(bodies, 1)), encoding="utf-8")
            self.docs.append(dict(document_id=doc_id, document_date=day, historical=historical,
                                  synthetic=True, title=f"Synthetic {doc_id}", quality="synthetic_test",
                                  ocr_file=f"{doc_id}.md", pages=len(bodies)))

    def cleanup_fixture(self):
        assert self.root.resolve().parent == TEMP_ROOT.resolve()
        for path in self.root.iterdir():
            path.unlink()
        self.root.rmdir()

    def build(self, docs=None):
        self.manifest_path.write_text(json.dumps({"documents": self.docs if docs is None else docs}), encoding="utf-8")
        return build_index(self.index, manifest_path=self.manifest_path, ocr_dir=self.root, pdf_dir=self.root)

    def test_real_fts_bounded_snippet_and_page_provenance(self):
        metadata = self.build()
        repo = BankRepository(self.index)
        hit, = repo.search("уникальнаястраница", 1)
        self.assertEqual((hit["page"], hit["locator"]), (2, "CURRENT:pdf-page:2"))
        self.assertEqual(hit["document_date"], "2026-09-11")
        self.assertEqual(hit["corpus_version"], metadata["corpus_version"])
        self.assertEqual(hit["source_kind"], "synthetic")
        self.assertTrue(hit["synthetic"])
        self.assertLessEqual(len(repo.search("ставка")[0]["text"]), 600)
        self.assertIsInstance(hit["rank"], float)
        self.assertEqual(repo.search("уникальнаястраниц"), repo.search("уникальнаястраниц"))

    def test_unknown_date_never_becomes_bank_position(self):
        stats = self.build()
        self.assertEqual(stats["undated_pages"], 1)
        self.assertEqual(BankRepository(self.index).search_with_status("недатированныймаркер"), {"status": "not_found", "passages": []})

    def test_historical_cannot_be_promoted(self):
        self.build()
        result = BankRepository(self.index).search_with_status("историческиймаркер")
        self.assertEqual(result["status"], "historical")
        self.assertTrue(result["passages"][0]["historical"])

    def test_missing_no_match_and_empty_query(self):
        repo = BankRepository(self.index)
        self.assertEqual(repo.search_with_status("ставка")["status"], "not_checked")
        self.assertFalse(self.index.exists())
        self.build()
        self.assertEqual(repo.search_with_status("неттакогослова")["status"], "not_found")
        self.assertEqual(repo.search("***"), [])

    def test_ambiguous_and_invalid_dates_rejected_without_replacing_index(self):
        self.build()
        before = self.index.read_bytes()
        for value in ["2026-08", "2026-02-30", "11.09.2026", "2026-09-11/2026-09-12", "", 2026]:
            docs = copy.deepcopy(self.docs)
            docs[0]["document_date"] = value
            with self.subTest(value=value), self.assertRaises(CorpusError):
                self.build(docs)
            self.assertEqual(self.index.read_bytes(), before)

    def test_query_limits_and_literal_fts_input(self):
        self.build()
        repo = BankRepository(self.index)
        for query, limit in [("x" * 257, 3), ("a " * 13, 3), (None, 3), ("x", 0), ("x", 11), ("x", True)]:
            with self.subTest(query=query, limit=limit), self.assertRaises(CorpusError):
                repo.search(query, limit)
        self.assertEqual(repo.search('"; DROP TABLE passages; --'), [])
        self.assertTrue(repo.search("ставка"))
        self.assertEqual(repo.search("NOT NEAR (x) : *"), [])

    def test_real_requires_pdf_and_verified_date(self):
        docs = copy.deepcopy(self.docs)
        docs[0]["synthetic"] = False
        with self.assertRaises(CorpusError):
            self.build(docs)
        docs[0]["date_basis"] = "verified_pdf"
        docs[0]["pdf_file"] = "missing.pdf"
        with self.assertRaises(CorpusError):
            self.build(docs)
        (self.root / "missing.pdf").write_bytes(b"%PDF-1.4\nsynthetic placeholder used only in test")
        self.build(docs)
        self.assertEqual(BankRepository(self.index).search("уникальнаястраница")[0]["source_kind"], "bank")

    def test_page_and_path_validation(self):
        for key, value in [("pages", 3), ("ocr_file", "../CURRENT.md"), ("document_id", "bad:id")]:
            docs = copy.deepcopy(self.docs)
            docs[0][key] = value
            with self.subTest(key=key), self.assertRaises(CorpusError):
                self.build(docs)
        (self.root / "CURRENT.md").write_text("## Страница 2\nwrong numbering", encoding="utf-8")
        with self.assertRaises(CorpusError):
            self.build()

    def test_rank_and_limits_stable_and_index_is_read_only(self):
        self.build()
        before = self.index.read_bytes()
        repo = BankRepository(self.index)
        hits = repo.search("инфляция", 1)
        self.assertEqual(len(hits), 1)
        ranked = repo.search("инфляция", 10)
        self.assertEqual([p["rank"] for p in ranked], sorted(p["rank"] for p in ranked))
        self.assertEqual(hits, ranked[:1])
        self.assertEqual(repo.search_with_status("уникальнаястраница")["status"], "found")
        self.assertEqual(self.index.read_bytes(), before)

    def test_corrupt_index_is_error_not_false_absence(self):
        self.index.write_text("not SQLite", encoding="utf-8")
        with self.assertRaises(CorpusError):
            BankRepository(self.index).search("ставка")

    def test_fts5_available_and_all_pages_indexed(self):
        # FTS5 availability itself is exercised by every successful build.
        self.build()
        with closing(sqlite3.connect(self.index)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM passage_fts").fetchone()[0], 4)

    def test_unsupported_schema_is_error(self):
        self.build()
        with closing(sqlite3.connect(self.index)) as db, db:
            db.execute("UPDATE metadata SET schema='unsupported'")
        with self.assertRaises(CorpusError):
            BankRepository(self.index).search("ставка")


if __name__ == "__main__":
    unittest.main()
