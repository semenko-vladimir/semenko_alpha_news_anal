"""Operator-only bounded refresh/status CLI. Output contains metadata only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sqlite3
import sys

from .repository import NewsRepository, NewsService, NewsServiceError


def _clock() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operator news refresh and safe status")
    sub = parser.add_subparsers(dest="command", required=True)
    refresh = sub.add_parser("refresh", help="fetch once from the fixed CBR RSS source")
    refresh.add_argument("--database", required=True)
    status = sub.add_parser("status", help="read safe metadata without network access")
    status.add_argument("--database", required=True)
    status.add_argument("--stale-after-seconds", type=int, default=86400)
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            result = NewsRepository.status_snapshot(args.database, stale_after_seconds=args.stale_after_seconds)
        else:
            result = NewsService(NewsRepository(args.database)).fetch_and_ingest()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("status", "ok") == "ok" else 2
    except NewsServiceError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2
    except (OSError, sqlite3.Error):
        print(json.dumps({"status": "error", "error": "database_unavailable"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
