"""Offline MVP acceptance. No SSH, external HTTP, credentials or real model calls."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
AREA = ROOT / '.tmp/acceptance'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', required=True)
    args = parser.parse_args()
    out = AREA / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
    out.mkdir(parents=True)
    # Set named, non-secret settings only. Never enumerate inherited environment.
    os.environ.update(ALPHA_DB_PATH=str(out / 'import.sqlite3'), ALPHA_ANALYSIS_ENGINE='deterministic_demo',
                      ALPHA_DEMO_MODE='0', ALPHA_MVP_ALLOW_LIVE='0', ALPHA_MVP_ALLOW_CLIENT_DATA='0',
                      PYTHONDONTWRITEBYTECODE='1', TEMP=str(out), TMP=str(out))
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(ROOT), str(ROOT / 'backend'), str(ROOT / 'tests/mvp')]
    os.chdir(out)
    proof = {'started_at': utc(), 'offline': True, 'model': 'explicit_fake', 'live_calls': 0,
             'status': 'running', 'checks': [], 'output': str(out.relative_to(ROOT))}

    def persist():
        (out / 'result.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')

    def check(name, fn, owner='APP'):
        item = {'name': name, 'started_at': utc(), 'repair_owner': owner}
        proof['checks'].append(item)
        try:
            item['evidence'] = fn()
            item.update(status='passed', repair_owner=None)
        except Exception as exc:
            item.update(status='blocked' if isinstance(exc, (FileNotFoundError, PermissionError)) else 'failed',
                        error_type=type(exc).__name__, error=str(exc)[:500])
            # Stack trace contains code locations only; no locals/source corpus.
            item['trace'] = traceback.format_tb(exc.__traceback__)
        item['finished_at'] = utc()
        persist()
        print(json.dumps({'check': name, 'status': item['status']}, ensure_ascii=False), flush=True)

    # Test processes only. External DNS/connect and the real broker entrypoint
    # are denied even if a regression accidentally attempts them.
    original_connect, original_connect_ex, original_dns = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def local(address):
        if not isinstance(address, tuple) or address[0] not in {'127.0.0.1', '::1', 'localhost'}:
            raise AssertionError('Offline acceptance forbids external networking')

    def connect(sock, address):
        local(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        local(address)
        return original_connect_ex(sock, address)

    def dns(host, *args, **kwargs):
        local((host, 0))
        return original_dns(host, *args, **kwargs)

    persist()
    from acceptance import Acceptance, run_existing_suite
    from backend.model_clients import BrokerModelClient
    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, 'connect', connect))
        stack.enter_context(patch.object(socket.socket, 'connect_ex', connect_ex))
        stack.enter_context(patch.object(socket, 'getaddrinfo', dns))
        stack.enter_context(patch.object(BrokerModelClient, 'live', side_effect=AssertionError('Real broker forbidden')))
        check('existing_backend_suite', lambda: run_existing_suite(ROOT, out), 'APP')
        acceptance = Acceptance(ROOT, out)
        try:
            check('local_inputs', acceptance.load_inputs, 'DATA/BANK/NEWS')
            if acceptance.config is not None:
                for name in ('real_http_contexts', 'dialogue_revision_idempotency', 'new_event_role_relevance',
                             'bank_coverage', 'errors_injection_access', 'restart_legacy_persistence'):
                    check(name, getattr(acceptance, name))
                check('existing_frontend_suite', acceptance.frontend_suite, 'UI')
                check('frontend_build', acceptance.build_frontend, 'UI')
                check('browser_ui_to_api', acceptance.browser, 'UI')
            check('input_integrity', acceptance.integrity, 'EVAL')
        finally:
            acceptance.close()
    bad = [c for c in proof['checks'] if c['status'] != 'passed']
    proof.update(status='failed' if any(c['status'] == 'failed' for c in bad) else 'blocked' if bad else 'passed',
                 finished_at=utc(), mvp_complete=False,
                 exclusions=['live_model: assigned LIVE stage only; not called',
                             'news network ingestion: existing local corpus only',
                             'reproducible_launch and Gate9: controller stages, not accepted here'])
    proof['artifacts'] = {str(p.relative_to(out)): {'sha256': sha(p), 'bytes': p.stat().st_size,
                        'mtime_utc': datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()}
                          for p in out.rglob('*') if p.is_file() and p.suffix in {'.log', '.json', '.png'} and p.name != 'result.json'}
    code = [Path(__file__), *list((ROOT / 'tests/mvp').glob('*.*')),
            *list((ROOT / 'backend').rglob('*.py')), *list((ROOT / 'frontend/src').rglob('*.*')),
            ROOT / 'frontend/package.json', ROOT / 'frontend/vite.config.ts']
    proof['code_sha256'] = {str(p.relative_to(ROOT)): sha(p) for p in code if p.is_file() and '__pycache__' not in p.parts and '.tmp' not in p.relative_to(ROOT).parts}
    persist()
    print(json.dumps({'status': proof['status'], 'result': str((out / 'result.json').relative_to(ROOT)),
                      'mvp_complete': False, 'live_calls': 0}, ensure_ascii=False), flush=True)
    return 1 if proof['status'] == 'failed' else 2 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
