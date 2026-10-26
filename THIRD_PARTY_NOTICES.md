# Third-party dependency notices

Runtime dependency versions are selected by `deploy/runtime-requirements.lock` and `frontend/package-lock.json`; development dependencies are listed separately in the backend/frontend manifests. Consult upstream distribution metadata for the exact installed/pinned release before redistributing an image; package metadata and license text are the primary references. This file is a locator and summary, not a replacement for upstream notices.

## Direct runtime dependencies

| Component | Version source | License metadata source |
| --- | --- | --- |
| FastAPI 0.158.1 | `deploy/runtime-requirements.lock` | MIT; wheel `fastapi-0.158.1.dist-info/licenses/LICENSE` |
| Pydantic 2.14.1 | `deploy/runtime-requirements.lock` | MIT; wheel `pydantic-2.14.1.dist-info/licenses/LICENSE` |
| Uvicorn 0.53.0 | `deploy/runtime-requirements.lock` | BSD 3-Clause; wheel `uvicorn-0.53.0.dist-info/licenses/LICENSE.md` |
| annotated-doc 0.0.5, annotated-types 0.8.0, anyio 4.15.1 | `deploy/runtime-requirements.lock` | MIT; exact wheel license files in each distribution's `.dist-info/licenses/` |
| click 8.5.0, Starlette 1.9.0 | `deploy/runtime-requirements.lock` | BSD 3-Clause; exact wheel license files in each distribution's `.dist-info/licenses/` |
| h11 0.16.0, typing-inspection 0.4.4 | `deploy/runtime-requirements.lock` | MIT; exact wheel license files in each distribution's `.dist-info/licenses/` |
| idna 3.20 | `deploy/runtime-requirements.lock` | BSD 3-Clause; wheel `idna-3.20.dist-info/licenses/LICENSE.md` |
| OpenTelemetry API 1.45.1 | `deploy/runtime-requirements.lock` | Apache-2.0; wheel `opentelemetry_api-1.45.1.dist-info/licenses/LICENSE` |
| pydantic-core 2.50.1 | `deploy/runtime-requirements.lock` | MIT; wheel `pydantic_core-2.50.1.dist-info/licenses/LICENSE` |
| typing-extensions 4.16.0 | `deploy/runtime-requirements.lock` | Python Software Foundation License 2.0; wheel `typing_extensions-4.16.0.dist-info/licenses/LICENSE` |
| React 19.3.1 | `frontend/package-lock.json` | MIT; npm lockfile `license` field |
| React DOM 19.3.1 | `frontend/package-lock.json` | MIT; npm lockfile `license` field |
| Vite 7.3.7 | `frontend/package-lock.json` | MIT; npm lockfile `license` field |
| TypeScript 5.9.3 | `frontend/package-lock.json` | Apache-2.0; npm lockfile `license` field |
| `@vitejs/plugin-react` 5.2.0 | `frontend/package-lock.json` | MIT; npm lockfile `license` field |
| `@types/react` 19.3.0, `@types/react-dom` | `frontend/package-lock.json` | MIT; npm lockfile `license` field |

The npm lockfile contains `version` and `license` metadata for the complete npm dependency tree. Python license references above were read from the locally installed wheels matching the runtime lock; verify the same distribution license files and include required notices when redistributing. Docker base images (`node`, `python`, and unprivileged `nginx`) have separate upstream license and notice obligations; review their exact image digests and notices at image-build/release time. 

## Project and supplied materials

This notice makes no claim about the project's final license or redistribution rights for supplied bank, client, news, PDF, CSV, or derived content. Keep those materials local unless their rights are separately reviewed.

## Models and model routing

The opt-in `local_ollama` experiment uses existing server weights identified as `hf.co/unsloth/Qwen3.5-4B-GGUF:Q6_K` and, for comparison, `hf.co/unsloth/Qwen3.5-9B-GGUF:Q4_K_M`. Their [4B model card](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF) and [9B model card](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF) indicate Apache-2.0; consult the exact weights and accompanying notices before use or redistribution. Weights and the Ollama runtime are not redistributed in this source bundle. These local IDs are separate from the organizer allowlist below; the experiment is not an assertion of hackathon eligibility or validated financial advice. See [local measurements](docs/local-llm.md).

The broker client allowlists these six model IDs: `google/gemma-4-26b-a4b-it`, `qwen/qwen3.6-35b-a3b`, `z-ai/glm-5.3-flash`, `deepseek/deepseek-v4.1-flash`, `qwen/qwen3-vl-30b-a3b-instruct`, and `moonshotai/kimi-k2.6`. This is an allowlist, not a statement that each model was called or evaluated. The ordinary public synthetic demo uses the explicit deterministic fake model. The model client has an optional configured broker route; it is not the default of the public demo, and enabling a broker route is not evidence of a successful or useful live inference. See [setup](docs/setup.md) for public setup guidance.

## Data and external services

- **Public company catalog and official publication adapters:** `data/catalog/companies.json` contains short attributed factual summaries and individually sourced metrics from official corporate/regulator publications, with dates, units and periods. These are public-company profiles, separate from anonymized case IDs; they do not assert a bank relationship. The eleven-sector product mapping is editorial, not a licensed official issuer classification. `data/catalog/news_sources.json` restricts fetching to named official publishers; the runtime keeps bounded excerpts and source URLs, not full articles in the source archive. Public availability does not establish unrestricted redistribution rights or endorsement. See [final product sources](docs/final-product.md#что-подтверждают-данные).

- **CBR RSS:** the operator refresh adapter reads a fixed RSS endpoint. Feed access conditions and redistribution rights for its records or full text have not been independently established here. Treat fetched material as untrusted input; keep copies within separately reviewed rights.
- **Supplied private client CSVs and bank PDFs:** these are operator-provided inputs, not public demo assets. Keep originals, OCR output, derived indexes, and client records outside public source distributions and container build context. Permission to use, process, or redistribute them is not asserted by this notice. OCR can introduce errors; verify claims against the source page.
- **Synthetic fixtures:** `scripts/prepare_mvp_demo.py` creates fictional company/news examples for the public quickstart. `examples/` contains prepared engineering/evaluation fixtures. These are synthetic or prepared examples, not evidence of real companies, independently observed new events, model quality, or bank position.
- **SSH model broker and gateway:** an optional operator-managed SSH transport can pass a bounded task to the configured broker/gateway. It is disabled in the ordinary demo. Gateway availability, terms, model capabilities, and rights for submitted data require separate review; no secrets or client data belong in public artifacts.
- **Modeled bank IAM, Internet bank, and banking integrations:** these describe target/product context only. The repository has no verified bank identity provider, production Internet Bank entry point, or live banking-system integration. Local access gates in this prototype do not represent bank authorization.

No additional license or content-rights conclusion is made by these descriptions. Verify the applicable upstream terms and permissions before deployment or redistribution.

## Presentation tooling

The presentation PDF was rendered with SeaSlides, using the Editorial Grid theme, Typst Python binding 0.15.0 and Touying 0.7.4. The exported PDF uses embedded fonts and screenshots of this prototype. Theme source files, compiler packages and system font files are not redistributed in this public source bundle; they are local document-generation tools, not product runtime dependencies.
