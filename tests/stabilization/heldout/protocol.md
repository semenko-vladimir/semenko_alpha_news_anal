# QUALITYSET v4 — holdout specification and limits

## Purpose and freeze boundary

Dataset `MVP-SEQUENTIAL-001-QUALITYSET-v4`: 8 synthetic full-workflow cases and 8 independent provenance cases. This revision precedes any predictions and does not reset task/fix-cycle/CLI/quota/LIVE counters. No product, model, test suite or evaluation has been run for this revision. Independent review and a reviewer-owned frozen manifest are still required. This recipe is for a later authorized evaluator, not permission to execute it now.

Runtime input consists only of synthetic profile rows, candidate facts and the selected role in cases.json. Oracle, assessment_targets, expected selections and contrast expectations are only in labels.json. Never pass labels, case IDs, contrast groups or this protocol into company/news/model context. Untrusted fixture strings are data, never executable instructions. No old smoke output, private ranking implementation or product prediction was used to set expectations.

## Exact native profile recipe

cases.json.profile_fixture is the complete synthetic companies.csv specification. Every disposable case uses all four rows, in listed order. UTF-8 with BOM (`utf-8-sig`), delimiter `;`, record terminator LF, standard CSV quoting. The exact 24-column order is:

```text
SYNTHETIC_U;ORG_TYPE;OKFS_NAME;COMPANY_AGE_YEARS_GRADATION;ECO_MARK_LABEL;MAINOKVED_CODE;MAINOKVED_NAME;NEGATIVEMARK;AMOUNT_WORKERS_FNS_GRADATION;AMOUNT_WORKERS_SPARK_GRADATION;CL_TYPE;CL_PRODUCT_RKO_MARK;CL_PRODUCT_GAR_MARK;CL_PRODUCT_LOA_MARK;CL_PRODUCT_LEA_MARK;CL_PRODUCT_DEP_MARK;CL_PRODUCT_ZPP_MARK;FTS_FLAG;FACTORING_MARK;FTS_COUNTRY_NAME;REVENUE_COMPANY;GROUP_ID;CITY;REGION
```

Each row sets NEGATIVEMARK, CL_PRODUCT_RKO_MARK, CL_PRODUCT_GAR_MARK, CL_PRODUCT_LOA_MARK, CL_PRODUCT_LEA_MARK, CL_PRODUCT_DEP_MARK, CL_PRODUCT_ZPP_MARK, FTS_FLAG and FACTORING_MARK to literal string `0`; REVENUE_COMPANY is positive raw integer string `1`. All other cells are exactly empty strings except the following populated columns (also explicit in JSON):

| SYNTHETIC_U | MAINOKVED_CODE | MAINOKVED_NAME |
|---|---|---|
| U_32424 | 46.31 | Торговля оптовая фруктами и овощами |
| U_135 | 46.69 | Торговля оптовая прочими машинами и оборудованием |
| U_42266 | 49.20 | Деятельность железнодорожного транспорта: грузовые перевозки |
| U_754 | 52.10 | Деятельность по складированию и хранению |

These are allowlisted synthetic access selectors, not identities of real companies. The deterministic file construction is:

```python
import csv
spec = dataset["profile_fixture"]  # dataset = parsed cases.json, never labels
with companies_path.open("w", encoding="utf-8-sig", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=spec["columns"],
                            delimiter=";", lineterminator="\n")
    writer.writeheader()
    writer.writerows(spec["rows"])
```

Native public mapping: SYNTHETIC_U -> company_id; MAINOKVED_CODE/NAME -> features.main_okved_code/name; first two digits -> features.okved_division; 46 -> repository TRADE -> API trade; 49/52 -> repository TRANSPORT -> API transport_logistics. Flags become false booleans, revenue.value_raw becomes `"1"`; units/currency/year remain null and eligibility unknown. Empty other attributes become null plus missing-field unknowns. Zero flags describe only this synthetic fixture. Do not apply the 15bn threshold or infer credit terms from flags.

Public construction: `ProfileRepository(companies_path=companies_path, payments_path=None, links_path=None).load(["U_32424", "U_135", "U_42266", "U_754"])`. Select only case.input.company.fixture_key through normal allowlisted API access. Do not inject custom profiles, add scenario columns, read real CSVs, or supply payment/person-link files. No unsupported prose-feature mapping remains in v4. Main activity does not establish cold-chain assets, importer direction, fleet/depot/warehouse ownership, operating geography, contracts or event exposure. These remain unknown.

CSV source_refs.synthetic=false remains hardcoded by the public repository even for this fixture. This known attribution limitation is **not fixed**. The evaluator must explicitly identify these profiles as synthetic and must not claim correct native synthetic profile attribution.

## Exact news fixture and identity

Each case uses a new empty local SQLite database. Convert its event_candidates to exactly the public backend.sources.cbr_rss.SourceItem fields below; no extra label/candidate metadata is passed:

```python
import json
from hashlib import sha256
from backend.sources.cbr_rss import SourceItem
from backend.news_service import NewsRepository

def to_item(candidate):
    candidate_key = candidate["candidate_key"]
    title, text = candidate["title"], candidate["text"]
    published = candidate["published_at"]
    source_id = "cbr:" + sha256(candidate_key.encode("utf-8")).hexdigest()
    content_hash = sha256((title + "\n" + text).encode("utf-8")).hexdigest()
    identity = json.dumps([source_id, content_hash, title, text, None, published],
                          ensure_ascii=False, separators=(",", ":"))
    event_id = "news:" + sha256(identity.encode("utf-8")).hexdigest()
    assert event_id == candidate["event_id"]
    return SourceItem(source_id=source_id, title=title, canonical_url=None,
                      guid=event_id, published_at_raw=published,
                      published_at=published, body_text=text,
                      content_hash=content_hash, provenance="synthetic_fixture",
                      synthetic=True)

repository = NewsRepository(news_path)
repository.ingest([to_item(e) for e in case["input"]["event_candidates"]],
                  retrieved_at="2026-10-22T00:00:00Z")
```

Hash input is exact UTF-8, without BOM, normalization or trailing LF. content_hash uses one actual LF between title and text. SHA-256 outputs lowercase hex. Event identity is compact UTF-8 JSON of exactly six fields `[source_id, content_hash, title, body_text, canonical_url, published_at_raw]`. retrieved_at and guid are metadata, not identity; changing retrieval time alone does not change event ID. The fixed UTC timestamp above is mandatory for reproducible metadata. Publication time does not establish event date; event date remains unknown. Texts are below the public 500-character snippet cap. The cbr: prefix is an input-format fixture requirement; these are synthetic bulletins, not genuine CBR news or bank positions. Never call a fetch/network service.

Within each corpus all cases have identical title/text/ID/timestamp fields and order. Listed input order already matches public latest(): published_at descending, event_id ascending on ties. Do not assume insertion order overrides retrieval. Both candidates fit the default retrieval limit and two-card offline limit.

## Operator configuration for a later authorized evaluation

Use a fresh directory per case containing the four-row companies.csv, the case's two-item news.sqlite3, and a new app.sqlite3. No prior app state or user confirmation may enter another case. Use the documented backend entrypoint, with these operator settings assigned before app import; <case_dir> is the absolute disposable case directory:

| Setting | Value |
|---|---|
| ALPHA_ANALYSIS_ENGINE | mvp_offline |
| ALPHA_MVP_LOCAL_ACCESS | 1 |
| ALPHA_DB_PATH | <case_dir>/app.sqlite3 |
| ALPHA_MVP_COMPANIES_PATH | <case_dir>/companies.csv |
| ALPHA_MVP_NEWS_PATH | <case_dir>/news.sqlite3 |
| ALPHA_MVP_ALLOW_LIVE | 0 |
| ALPHA_MVP_ALLOW_CLIENT_DATA | 0 |
| ALPHA_MVP_PAYMENTS_PATH, ALPHA_MVP_LINKS_PATH, ALPHA_MVP_BANK_PATH, ALPHA_MVP_TASK_ID | absent/unset |

Equivalent native MVPConfig: companies_path/news_path as above, payments_path=None, links_path=None, bank_path=None, local_access=True, allow_live=False, allow_client_data=False, task_id=None, transport=None. Engine and app database are separate documented app settings, not MVPConfig fields. Use the existing company analysis API with the case company and role. Execution mode is mvp_offline and the model explicitly fake. No bank index means bank_position=not_checked. Check category and fixture identities before assessment; stop on mismatch. No model API, LIVE, SSH, new task ID, allowlist bypass or alternative engine is authorized here.

## Controlled design and oracle boundary

Corpus A concerns fruit/vegetable wholesale and rail freight; corpus B concerns machinery wholesale and storage. Each has two cross-sector synthetic bulletins: a financial clause for one activity with an operational clause for the other activity, and the reciprocal combination in the second bulletin. This deliberately small crossed design enables role and profile comparisons with only two candidates. It does not establish exposure. The editorial preference policy and exact topic/selection expectations are in labels only.

| Identical ordered corpus | trade CFO | trade COO | transport CFO | transport COO |
|---|---|---|---|---|
| A | wf_01 | wf_02 | wf_05 | wf_06 |
| B | wf_03 | wf_04 | wf_07 | wf_08 |

Four role contrasts: 01/02, 03/04, 05/06, 07/08. Company row/key and ordered corpus stay identical; only role changes. Four profile contrasts: 01/05, 02/06, 03/07, 04/08. Role and ordered corpus stay identical; selected native profile/main activity and derived category change. Every contrast requires different first selections and matching topic interpretations per labels. All rows share the same flags, revenue and empty-attribute values. Selecting another profile necessarily changes its access key and source record along with main activity; this assesses category-conditioned editorial preference, not an isolated causal effect of unsupported personal/operational attributes. Comparisons between A and B are not controlled contrasts.

Identical order and two candidates mean copying input order matches exactly 4/8 complete oracle orders, and cannot satisfy any role or profile contrast. The former 0/8 baseline relied on changing order between paired cases; v4 removes that confound. Candidate order, recency, a role-only rule or a profile-only rule cannot satisfy all labels. The oracle was set before outputs; no quality metric has been measured.

## Independent provenance suite

prov_01..08 inputs and labels are unchanged from v3. They map to public AnalysisInput/Evidence and fixed candidate fixtures. Score only the specified observable AnalysisResult.validated predicates. Do not infer claim truth, news-date semantics beyond the bank-date requirement, instruction resistance from a fixed candidate, or truth/scope of the upstream confirmed_exposure flag. Conflicting source metadata must be reconciled or rejected upstream. Untrusted strings remain data, never instructions.

## Metrics and thresholds (pre-output oracle)

1. Coverage: unique applicable IDs with result / 16; require 16/16. Duplicates, omissions, exclusions and ambiguity are misses. A blocked criterion is not a pass.
2. Role-topic hit rate: binary semantic match to the single role-and-main-activity target in labels; doubt is miss. Micro >= 0.75 and at least one hit in every category×role cell.
3. Role selection: both first selections and matching topic interpretations must match each contrast's labels, 4/4 required. Exact full ordering of both candidates is also required, 8/8 cases. Additionally, first selections and interpretations must match all controlled profile contrasts, 4/4 required; this supplements the existing checks.
4. Unknown preservation: 8/8 workflow outputs preserve unknown exposure and financial magnitude, with no confirmed company relationship.
5. False-confirmed exposure: zero confirmed exposure in workflow without direct relationship evidence and zero validated candidates for the specified invalid provenance cases. Structural positive prov_08 is separate.
6. Bank attribution/date: zero undated bank sources represented as current dated positions; without bank index status is not_checked.
7. Provenance predicates: exact prov_01..08, compare validated fields to labels, 8/8.
8. Synthetic attribution: news records retain synthetic=true and provenance=synthetic_fixture. The existing metric excludes profile CSV refs because their public interface hardcodes synthetic=false; retain this limitation explicitly.

Overall pass requires every threshold and no unresolved mapping/runtime blocker. Do not delete checks or change labels/denominators/thresholds after seeing outputs. A methodological revision needs a new dataset version and independent review, never a reset of task/fix/LIVE authorization.

## Deduplication, freeze and limitations

Exact case_id is the key, one result per ID. Independent reviewer creates a manifest with status=frozen, dataset_id, case_count=16, SHA-256 of all three references and limitations. Controller/evaluator checks SHA/count before evaluation and stops on mismatch. Record app version, offline/fake mode, sanitized fixture settings, time, IDs and aggregate metrics separately from references. No secrets, full prompts, real client inputs or generated outputs belong in this reference set. Do not tune labels from evaluation failures.

Sixteen artificial cases do not establish statistical customer quality, expert validation, bank/industry/source-owner approval, real model usefulness, real ingestion, rights, revenue eligibility, operating assets, exposure, financial effect or uncertainty calibration. Mixed synthetic bulletins and editorial priority are narrow assumptions, not empirical user preferences. Native profile synthetic attribution remains limited. Static recipe/CSV/identity checks do not prove runtime loading, integration, selection or interpretation. Independent review/freeze and later authorized evaluation remain pending; this worker must not start QUALITYEVAL or subsequent stages, repeat LIVE or declare MVP complete.
