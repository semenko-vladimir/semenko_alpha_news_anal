"""Serve the built UI and local offline MVP on one loopback port; no live LLM."""
import argparse
import os
from pathlib import Path
import socket
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]


def build_app(args):
    """Operator-selected local files only; keep writable state inside .tmp."""
    runtime = args.runtime.resolve()
    runtime.relative_to(ROOT / '.tmp')
    inputs = [p.resolve() for p in (args.companies, args.news, args.payments, args.links, args.bank) if p]
    for path in inputs:
        if not path.is_file():
            raise FileNotFoundError(f'Required local input does not exist: {path}')
        for target in (runtime / 'app.sqlite3', runtime / 'news.sqlite3'):
            if path == target or (target.exists() and path.samefile(target)):
                raise ValueError('Runtime database must not overwrite an input')
    if not (ROOT / 'frontend/dist/index.html').is_file():
        raise FileNotFoundError('Build the UI first: npm --prefix frontend ci; npm --prefix frontend run build')
    runtime.mkdir(parents=True, exist_ok=True)
    # Read-only consistent backup, including any committed source WAL state.
    with sqlite3.connect(args.news.resolve().as_uri() + '?mode=ro', uri=True) as source:
        with sqlite3.connect(runtime / 'news.sqlite3') as target:
            source.backup(target)
    # Only named, non-secret settings. Disable import-time demo/live selection.
    os.environ['ALPHA_ANALYSIS_ENGINE'] = 'deterministic_demo'
    os.environ['ALPHA_DEMO_MODE'] = '0'
    os.environ['ALPHA_DB_PATH'] = str(runtime / 'import.sqlite3')
    sys.path.insert(0, str(ROOT))
    from backend.app import make_app
    from backend.mvp.config import MVPConfig
    from fastapi.staticfiles import StaticFiles

    config = MVPConfig(companies_path=args.companies, payments_path=args.payments,
        links_path=args.links, news_path=runtime / 'news.sqlite3', bank_path=args.bank,
        local_access=True, allow_live=False, allow_client_data=False)
    app = make_app(runtime / 'app.sqlite3', analysis_engine='mvp_offline', mvp_config=config)
    if app.state.mvp_engine.blocker:
        raise ValueError('Local MVP inputs rejected: ' + app.state.mvp_engine.blocker)
    app.mount('/', StaticFiles(directory=ROOT / 'frontend/dist', html=True), name='frontend')
    return app


def options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8010)
    parser.add_argument('--runtime', type=Path, default=ROOT / '.tmp/mvp-local')
    parser.add_argument('--companies', type=Path, default=ROOT / '.tmp/mvp-synthetic/companies.csv')
    parser.add_argument('--payments', type=Path, default=ROOT / '.tmp/mvp-synthetic/payments.csv')
    parser.add_argument('--links', type=Path, default=ROOT / '.tmp/mvp-synthetic/links.csv')
    parser.add_argument('--news', type=Path, default=ROOT / '.tmp/mvp-synthetic/news.sqlite3')
    parser.add_argument('--bank', type=Path, default=None)
    return parser.parse_args()


def main():
    args = options()
    if not 1 <= args.port <= 65535:
        raise SystemExit('Port must be in 1..65535')
    # A second launch must fail before changing the first instance's files.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    app = build_app(args)
    import uvicorn
    print(f'Local MVP: http://127.0.0.1:{args.port}/ | mvp_offline | FAKE MODEL', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
