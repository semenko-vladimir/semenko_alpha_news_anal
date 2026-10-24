"""Create explicitly fictional local demo inputs; never read private datasets or keys."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.tmp/mvp-synthetic')
    parser.add_argument('--port', type=int, default=8087)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / '.tmp') or output == ROOT / '.tmp':
        parser.error('Output must be a new child directory of this project .tmp')
    if not 1 <= args.port <= 65535:
        parser.error('Port must be in 1..65535')
    reference = ROOT / 'examples/quality-010/cases.json'
    if output.exists():
        parser.error('Output already exists; preserved. Choose a new directory.')
    cases = json.loads(reference.read_text(encoding='utf-8'))
    assert cases['synthetic'] is True and cases['profile_fixture']['synthetic'] is True
    output.mkdir(parents=True)
    profile = cases['profile_fixture']
    with (output / 'companies.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=profile['columns'], delimiter=';')
        writer.writeheader()
        writer.writerows(profile['rows'])
    # Empty, well-formed observations do not invent payment or ownership links.
    (output / 'payments.csv').write_text('BENEFICIARY;PAYER;PAY_AMT;PAY_CNT\n', encoding='utf-8')
    (output / 'links.csv').write_text('SYNTHETIC_F;CITY_NAME;RC_NAME;SYNTHETIC_U\n', encoding='utf-8')
    sys.path.insert(0, str(ROOT))
    from backend.news_service import NewsRepository
    from backend.sources.cbr_rss import SourceItem
    news = NewsRepository(output / 'news.sqlite3')
    selected = [case for case in cases['cases']
                if case['case_id'] in ('new-A-1-trade-CFO', 'new-B-1-trade-CFO')]
    items = []
    for case in selected:
        for candidate in case['input']['event_candidates']:
            title, text = candidate['title'], candidate['text']
            source_id = 'cbr:' + hashlib.sha256(candidate['candidate_key'].encode()).hexdigest()
            content_hash = hashlib.sha256((title + '\n' + text).encode()).hexdigest()
            items.append(SourceItem(source_id, title, None, candidate['event_id'],
                candidate['published_at'], candidate['published_at'], text,
                content_hash, 'synthetic_fixture', True))
    news.ingest(items, retrieved_at='2026-10-23T12:00:00Z')
    # All files are operator inputs mounted read-only, outside the Docker context.
    paths = {'MVP_COMPANIES_FILE': 'companies.csv', 'MVP_PAYMENTS_FILE': 'payments.csv',
             'MVP_LINKS_FILE': 'links.csv', 'MVP_NEWS_DB_FILE': 'news.sqlite3'}
    lines = [key + '=' + (output / name).as_posix() for key, name in paths.items()]
    lines += ['ALPHA_ANALYSIS_ENGINE=mvp_offline', 'ALPHA_MVP_LOCAL_ACCESS=1',
              'MVP_WEB_PORT=' + str(args.port)]
    (output / 'compose.env').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (output / 'README.txt').write_text(
        'SYNTHETIC ONLY: all profiles and news are fictional. No bank position.\n'
        'Offline FAKE MODEL, local demo access; no API credential or network generation.\n'
        'Existing anonymized ID selectors are reused only for the public server contract.\n', encoding='utf-8')
    names = [*paths.values(), 'compose.env', 'README.txt']
    manifest = {'synthetic_only': True, 'company_count': len(profile['rows']),
        'news_count': len(items), 'source_reference_sha256': sha(reference),
        'files': {name: sha(output / name) for name in names},
        'private_inputs_read': 0, 'live_calls': 0, 'bank_index': None}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'created', 'synthetic_only': True, 'directory': str(output),
                      'company_count': manifest['company_count'], 'news_count': len(items)}))


if __name__ == '__main__':
    main()
