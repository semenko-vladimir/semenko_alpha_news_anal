from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

ALLOWED_COMPANIES = ("U_32424", "U_135", "U_42266", "U_754")


@dataclass(frozen=True)
class MVPConfig:
    companies_path: str | Path | None = None
    payments_path: str | Path | None = None
    links_path: str | Path | None = None
    news_path: str | Path | None = None
    bank_path: str | Path | None = None
    local_access: bool = False
    allow_live: bool = False
    allow_client_data: bool = False
    task_id: str | None = None
    # Test-only transport injection; HTTP cannot select it or any paths/policy.
    transport: Any = None

    @classmethod
    def from_environment(cls):
        # Read only named, non-secret operator settings. Never enumerate env.
        return cls(companies_path=os.getenv("ALPHA_MVP_COMPANIES_PATH"),
                   payments_path=os.getenv("ALPHA_MVP_PAYMENTS_PATH"),
                   links_path=os.getenv("ALPHA_MVP_LINKS_PATH"),
                   news_path=os.getenv("ALPHA_MVP_NEWS_PATH"),
                   bank_path=os.getenv("ALPHA_MVP_BANK_PATH"),
                   local_access=os.getenv("ALPHA_MVP_LOCAL_ACCESS") == "1",
                   allow_live=os.getenv("ALPHA_MVP_ALLOW_LIVE") == "1",
                   allow_client_data=os.getenv("ALPHA_MVP_ALLOW_CLIENT_DATA") == "1",
                   task_id=os.getenv("ALPHA_MVP_TASK_ID"))
