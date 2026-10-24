"""Streaming semicolon CSV reader. No network, persistence or default dataset paths."""

import csv
import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


FLAGS = (
    "NEGATIVEMARK", "CL_PRODUCT_RKO_MARK", "CL_PRODUCT_GAR_MARK",
    "CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK", "CL_PRODUCT_DEP_MARK",
    "CL_PRODUCT_ZPP_MARK", "FTS_FLAG", "FACTORING_MARK",
)
COMPANY_FIELDS = (
    "SYNTHETIC_U", "ORG_TYPE", "OKFS_NAME", "COMPANY_AGE_YEARS_GRADATION",
    "ECO_MARK_LABEL", "MAINOKVED_CODE", "MAINOKVED_NAME", "NEGATIVEMARK",
    "AMOUNT_WORKERS_FNS_GRADATION", "AMOUNT_WORKERS_SPARK_GRADATION", "CL_TYPE",
    *FLAGS[1:], "FTS_COUNTRY_NAME", "REVENUE_COMPANY", "GROUP_ID", "CITY", "REGION",
)
BASE_UNKNOWNS = [
    "eligibility_unknown: revenue units, currency and reporting year are unconfirmed; 15bn threshold not applied",
    "profile_snapshot_date_unknown",
    "registration_location_is_not_operating_geography",
    "main_okved_does_not_establish_product_or_event_exposure",
    "product_flags_do_not_establish_balances_rates_or_terms",
    "role_requires_explicit_user_selection",
    "group_label_does_not_establish_control_or_consolidation",
]


class ProfileDataError(ValueError):
    """Invalid local source; messages contain locators, never source cell values."""


@dataclass
class CompanyProfile:
    company_id: str
    category: str
    display_name: str
    version: str
    features: dict
    unknowns: list
    source_refs: list


def _id(value, prefix="U"):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"_[0-9]+", value):
        raise ProfileDataError("invalid_" + prefix + "_key")
    return value


def _integer(value, field):
    try:
        number = Decimal(value)
        if not number.is_finite() or number <= 0 or number != number.to_integral_value():
            raise InvalidOperation
        return int(number)
    except (InvalidOperation, ValueError, OverflowError):
        raise ProfileDataError("invalid_positive_integer:" + field) from None


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rows(path, fields):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, delimiter=";", strict=True)
        header = next(reader, None)
        if header is None or len(set(header)) != len(header) or set(header) != set(fields):
            raise ProfileDataError("invalid_header:" + path.name)
        try:
            for index, cells in enumerate(reader, start=2):
                if len(cells) != len(header):
                    raise ProfileDataError(f"invalid_row_width:{path.name}:record:{index}")
                yield index, dict(zip(header, (cell.strip() for cell in cells)))
        except csv.Error:
            raise ProfileDataError("invalid_csv:" + path.name) from None


class ProfileRepository:
    """load replaces access scope atomically; failed loads leave no profiles accessible."""

    def __init__(self, companies_path, *, payments_path=None, links_path=None):
        self.companies_path = Path(companies_path)
        self.payments_path = Path(payments_path) if payments_path is not None else None
        self.links_path = Path(links_path) if links_path is not None else None
        self._profiles = {}

    def get(self, company_id) -> CompanyProfile:
        # Return isolated snapshots: callers cannot mutate cached profile/version inputs.
        return deepcopy(self._profiles[company_id])

    def load(self, allowed_ids) -> list[CompanyProfile]:
        self._profiles = {}
        if isinstance(allowed_ids, (str, bytes)):
            raise ProfileDataError("allowed_ids_must_be_collection")
        ids = sorted({_id(value) for value in allowed_ids})
        if not ids:
            return []
        paths = {"companies": self.companies_path}
        if self.payments_path is not None:
            paths["payments"] = self.payments_path
        if self.links_path is not None:
            paths["links"] = self.links_path
        hashes = {kind: _hash(path) for kind, path in paths.items()}
        profiles = {}

        def ref(kind, index):
            return {"source_id": f"csv:{kind}:{hashes[kind]}",
                    "locator": f"{paths[kind].name}#record={index}",
                    "fingerprint": "sha256:" + hashes[kind], "synthetic": False}

        for index, row in _rows(self.companies_path, COMPANY_FIELDS):
            key = _id(row["SYNTHETIC_U"])
            if key not in ids:
                continue
            if key in profiles:
                raise ProfileDataError("duplicate_or_conflicting_company_key")
            code = row["MAINOKVED_CODE"]
            if not re.fullmatch(r"[0-9]{2}(?:\.[0-9]{1,2}){0,2}", code):
                raise ProfileDataError("invalid_main_okved")
            division = int(code[:2])
            category = "TRADE" if 45 <= division <= 47 else "TRANSPORT" if 49 <= division <= 53 else None
            if category is None:
                raise ProfileDataError("company_outside_mvp_categories")
            flags = {}
            for name in FLAGS:
                if row[name] not in ("0", "1", "0.0", "1.0"):
                    raise ProfileDataError("invalid_binary_flag:" + name)
                flags[name] = row[name] in ("1", "1.0")
            revenue = _integer(row["REVENUE_COMPANY"], "REVENUE_COMPANY")
            if not row["MAINOKVED_NAME"]:
                raise ProfileDataError("missing_main_okved_name")
            unknowns = list(BASE_UNKNOWNS)
            unknowns.extend("missing:" + field for field in COMPANY_FIELDS if not row[field])
            if flags["FTS_FLAG"] != bool(row["FTS_COUNTRY_NAME"]):
                unknowns.append("fts_flag_country_incomplete_or_inconsistent")
            features = {
                "main_okved_code": code, "main_okved_name": row["MAINOKVED_NAME"],
                "okved_division": code[:2], "eligibility": "unknown",
                "revenue": {"value_raw": str(revenue), "units": None, "currency": None, "year": None},
                "flags": flags,
                "attributes": {field: row[field] or None for field in COMPANY_FIELDS
                               if field not in FLAGS and field not in ("SYNTHETIC_U", "REVENUE_COMPANY", "MAINOKVED_CODE", "MAINOKVED_NAME")},
            }
            profiles[key] = CompanyProfile(key, category, "Обезличенная компания " + key,
                                           "", features, unknowns, [ref("companies", index)])
        if set(profiles) != set(ids):
            raise ProfileDataError("allowed_company_missing")

        if self.payments_path is not None:
            stats = {key: {"incoming": set(), "outgoing": set(), "self_rows": 0,
                           "observed_rows": 0} for key in ids}
            pairs = set()
            for index, row in _rows(self.payments_path, ("BENEFICIARY", "PAYER", "PAY_AMT", "PAY_CNT")):
                payer, beneficiary = _id(row["PAYER"]), _id(row["BENEFICIARY"])
                touched = {payer, beneficiary} & set(profiles)
                if not touched:
                    continue
                _integer(row["PAY_AMT"], "PAY_AMT")
                _integer(row["PAY_CNT"], "PAY_CNT")
                pair = payer, beneficiary
                if pair in pairs:
                    raise ProfileDataError("duplicate_or_conflicting_selected_payment_pair")
                pairs.add(pair)
                for key in touched:
                    stats[key]["observed_rows"] += 1
                    if payer == beneficiary:
                        stats[key]["self_rows"] += 1
                    elif key == payer:
                        stats[key]["outgoing"].add(beneficiary)
                    else:
                        stats[key]["incoming"].add(payer)
                    profiles[key].source_refs.append(ref("payments", index))
            for key, stat in stats.items():
                profiles[key].features["payment_observations"] = {
                    "incoming_unique_other_ids": len(stat["incoming"]),
                    "outgoing_unique_other_ids": len(stat["outgoing"]),
                    "self_rows": stat["self_rows"], "observed_rows": stat["observed_rows"],
                }
                profiles[key].unknowns.extend([
                    "payment_units_currency_window_unknown", "payments_do_not_establish_deliveries_or_purchase_shares",
                    "payment_counterparty_profiles_not_loaded", "absence_of_payments_does_not_establish_absence_of_activity",
                ])
        else:
            for profile in profiles.values():
                profile.unknowns.append("payment_source_not_loaded")

        if self.links_path is not None:
            people = {key: set() for key in ids}
            for index, row in _rows(self.links_path, ("SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U")):
                key, person = _id(row["SYNTHETIC_U"]), _id(row["SYNTHETIC_F"], "F")
                if key not in profiles:
                    continue
                if person in people[key]:
                    raise ProfileDataError("duplicate_or_conflicting_selected_person_link")
                people[key].add(person)
                profiles[key].source_refs.append(ref("links", index))
            for key in ids:
                profiles[key].features["observed_linked_person_count"] = len(people[key])
                profiles[key].unknowns.append("person_links_do_not_establish_role_ownership_or_control")
        else:
            for profile in profiles.values():
                profile.unknowns.append("person_link_source_not_loaded")

        if any(_hash(path) != hashes[kind] for kind, path in paths.items()):
            raise ProfileDataError("source_changed_during_load")
        for profile in profiles.values():
            payload = {"schema": "mvp003-profiles-v1", "sources": hashes,
                       "company_id": profile.company_id, "features": profile.features,
                       "unknowns": profile.unknowns, "refs": profile.source_refs}
            profile.version = "sha256:" + hashlib.sha256(json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
        self._profiles = profiles
        return [self.get(key) for key in ids]
