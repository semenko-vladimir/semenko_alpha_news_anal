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
