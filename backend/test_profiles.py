"""Synthetic tests invoking the production CSV repository (no private rows)."""

import csv
import importlib
import sys
import unittest
import uuid
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from profiles import CompanyProfile, ProfileDataError, ProfileRepository
from profiles.repository import COMPANY_FIELDS, FLAGS


def company(key="U_1", **changes):
    row = {name: "synthetic" for name in COMPANY_FIELDS}
    row.update({name: "0.0" for name in FLAGS})
    row.update(SYNTHETIC_U=key, MAINOKVED_CODE="46.20", MAINOKVED_NAME="Synthetic trade",
               REVENUE_COMPANY="16000000000.0", FTS_COUNTRY_NAME="")
    row.update(changes)
    return row


class ProfileTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".tmp/tests/profiles"
        scratch.mkdir(parents=True, exist_ok=True)
        # Python 3.14 tempfile mode 0700 can exclude the Windows sandbox SID.
        # Normal inherited-ACL directories stay inside the task's ignored scope.
        self.root = scratch / uuid.uuid4().hex
        self.root.mkdir()
        self.companies = self.root / "companies.csv"
        self.payments = self.root / "payments.csv"
        self.links = self.root / "links.csv"
        self.write(self.companies, COMPANY_FIELDS, [company(), company("U_2", MAINOKVED_CODE="49.4")])
        self.write(self.payments, ["PAYER", "BENEFICIARY", "PAY_AMT", "PAY_CNT"], [
            {"PAYER": "U_1", "BENEFICIARY": "U_999", "PAY_AMT": "1000.0", "PAY_CNT": "2"},
            {"PAYER": "U_2", "BENEFICIARY": "U_1", "PAY_AMT": "1000", "PAY_CNT": "1"},
            {"PAYER": "U_1", "BENEFICIARY": "U_1", "PAY_AMT": "1000", "PAY_CNT": "1"},
        ])
        self.write(self.links, ["SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"], [
            {"SYNTHETIC_F": "F_1", "CITY_NAME": "", "RC_NAME": "", "SYNTHETIC_U": "U_1"},
        ])
        self.repo = ProfileRepository(self.companies, payments_path=self.payments, links_path=self.links)

    def write(self, path, fields, rows):
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)

    def test_public_contract_and_safe_semantics(self):
        one, two = self.repo.load(["U_2", "U_1"])
        self.assertIsInstance(one, CompanyProfile)
        self.assertEqual(one.category, "TRADE")
        self.assertEqual(two.category, "TRANSPORT")
        self.assertEqual(one.features["main_okved_code"], "46.20")
        self.assertEqual(one.features["eligibility"], "unknown")
        self.assertIsNone(one.features["revenue"]["currency"])
        self.assertIsNone(one.features["revenue"]["units"])
        self.assertIsNone(one.features["revenue"]["year"])
        self.assertNotIn("role", asdict(one))
        self.assertEqual(one.features["payment_observations"], {
            "incoming_unique_other_ids": 1, "outgoing_unique_other_ids": 1,
            "self_rows": 1, "observed_rows": 3,
        })
        self.assertEqual(one.features["observed_linked_person_count"], 1)
        self.assertTrue(any("ownership" in text for text in one.unknowns))
        self.assertTrue(any("deliveries" in text for text in one.unknowns))
        self.assertEqual(len(one.source_refs), 5)
        self.assertEqual(one.source_refs[0]["locator"], "companies.csv#record=2")
        self.assertTrue(all(not ref["synthetic"] for ref in one.source_refs))
        # Files are fixtures, but the repository accurately treats loaded CSV as local data.
        self.assertNotIn("F_1", str(asdict(one)))
        self.assertNotIn("U_999", str(asdict(one)))

    def test_fingerprint_stable_and_changes_with_corpus(self):
        first = self.repo.load(["U_1"])[0].version
        self.assertEqual(first, self.repo.load(["U_1"])[0].version)
        self.write(self.companies, COMPANY_FIELDS, [company(REVENUE_COMPANY="99")])
        self.assertNotEqual(first, self.repo.load(["U_1"])[0].version)

    def test_access_scope_replacement_and_no_mutation(self):
        result = self.repo.load(["U_1"])[0]
        result.features["eligibility"] = "yes"
        self.assertEqual(self.repo.get("U_1").features["eligibility"], "unknown")
        with self.assertRaises(KeyError):
            self.repo.get("U_2")
        self.repo.load(["U_2"])
        with self.assertRaises(KeyError):
            self.repo.get("U_1")
        self.assertEqual(self.repo.load([]), [])
        with self.assertRaises(KeyError):
            self.repo.get("U_2")

    def test_missing_or_invalid_allowlist_fails_closed(self):
        for ids in (["U_999"], ["invalid"], "U_1"):
            self.repo.load(["U_1"])
            with self.assertRaises(ProfileDataError):
                self.repo.load(ids)
            with self.assertRaises(KeyError):
                self.repo.get("U_1")

    def test_duplicate_company_never_picks_first_even_identical(self):
        for second in (company(), company(REVENUE_COMPANY="20")):
            self.write(self.companies, COMPANY_FIELDS, [company(), second])
            with self.assertRaisesRegex(ProfileDataError, "conflicting_company"):
                self.repo.load(["U_1"])

    def test_conflicting_unselected_company_does_not_block_scope(self):
        self.write(self.companies, COMPANY_FIELDS, [company(), company("U_3"), company("U_3", REVENUE_COMPANY="1")])
        self.assertEqual(len(self.repo.load(["U_1"])), 1)

    def test_invalid_numeric_fields_and_flags(self):
        for value in ("NaN", "Infinity", "-1", "0", "1.5", "oops", ""):
            with self.subTest(value=value):
                self.write(self.companies, COMPANY_FIELDS, [company(REVENUE_COMPANY=value)])
                with self.assertRaises(ProfileDataError):
                    self.repo.load(["U_1"])
        self.write(self.companies, COMPANY_FIELDS, [company(FTS_FLAG="yes")])
        with self.assertRaisesRegex(ProfileDataError, "invalid_binary_flag"):
            self.repo.load(["U_1"])

    def test_invalid_categories_and_okved(self):
        for code in ("62.01", "46.200", "bad", ""):
            self.write(self.companies, COMPANY_FIELDS, [company(MAINOKVED_CODE=code)])
            with self.assertRaises(ProfileDataError):
                self.repo.load(["U_1"])

    def test_header_and_width_validation(self):
        for content in ("SYNTHETIC_U;SYNTHETIC_U\n", "SYNTHETIC_U\nU_1\n",
                        ";".join(COMPANY_FIELDS) + "\nU_1;extra\n"):
            self.companies.write_text(content, encoding="utf-8")
            with self.assertRaises(ProfileDataError):
                self.repo.load(["U_1"])

    def test_optional_sources_and_missing_data_remain_unknown(self):
        self.write(self.companies, COMPANY_FIELDS, [company(ECO_MARK_LABEL="", FTS_FLAG="1.0")])
        profile = ProfileRepository(self.companies).load(["U_1"])[0]
        self.assertIn("missing:ECO_MARK_LABEL", profile.unknowns)
        self.assertIn("fts_flag_country_incomplete_or_inconsistent", profile.unknowns)
        self.assertIn("payment_source_not_loaded", profile.unknowns)
        self.assertNotIn("payment_observations", profile.features)

    def test_repeated_payment_pair_and_bad_numeric_fail(self):
        fields = ["PAYER", "BENEFICIARY", "PAY_AMT", "PAY_CNT"]
        row = dict(zip(fields, ["U_1", "U_2", "1000", "1"]))
        for rows in ([row, row], [dict(row, PAY_CNT="1.1")], [dict(row, PAY_AMT="NaN")]):
            self.write(self.payments, fields, rows)
            with self.assertRaises(ProfileDataError):
                self.repo.load(["U_1"])

    def test_invalid_foreign_key_and_duplicate_person_link_fail(self):
        fields = ["SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"]
        row = dict(zip(fields, ["F_1", "", "", "U_1"]))
        for rows in ([row, row], [dict(row, SYNTHETIC_F="U_1")]):
            self.write(self.links, fields, rows)
            with self.assertRaises(ProfileDataError):
                self.repo.load(["U_1"])

    def test_missing_file_and_import_no_io(self):
        with patch("pathlib.Path.open", side_effect=AssertionError("unexpected file read")):
            importlib.reload(sys.modules["profiles"])
            repo = ProfileRepository(self.root / "missing.csv")
        with self.assertRaises(FileNotFoundError):
            repo.load(["U_1"])

    def test_mutating_source_is_rejected(self):
        from profiles.repository import _hash
        call_count = 0

        def changed_hash(path):
            nonlocal call_count
            call_count += 1
            return _hash(path) if call_count <= 3 else "changed"

        with patch("profiles.repository._hash", side_effect=changed_hash):
            with self.assertRaises(ProfileDataError):
                self.repo.load(["U_1"])


if __name__ == "__main__":
    unittest.main()
