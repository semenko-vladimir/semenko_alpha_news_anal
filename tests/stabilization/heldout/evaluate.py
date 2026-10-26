"""Frozen synthetic offline evaluation; predict never loads the answer key.

Run predict first, then score in a separate process. No network or real model.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def frozen(manifest_path):
    manifest = read(manifest_path)
    prefix = REFERENCE.resolve().relative_to(ROOT).as_posix()
    expected = {f'{prefix}/{name}' for name in ('cases.json', 'labels.json', 'protocol.md')}
    if manifest.get('status') != 'frozen' or set(manifest.get('files', {})) != expected:
        raise ValueError('Invalid freeze manifest')
    for relative, entry in manifest['files'].items():
        digest = entry.get('sha256') if isinstance(entry, dict) else entry
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or sha(ROOT / relative) != digest:
            raise ValueError('Frozen reference SHA mismatch')
    return manifest


def product_hashes():
    return {str(p.relative_to(ROOT)).replace('\\', '/'): sha(p)
            for p in (ROOT / 'backend').rglob('*.py') if '__pycache__' not in p.parts}


def predict_workflow(case, profile_spec, directory):
    from fastapi.testclient import TestClient
    from backend.app import make_app
    from backend.mvp.config import MVPConfig
    from backend.news_service import NewsRepository
    from backend.sources.cbr_rss import SourceItem

    inputs = case['input']
    company, role = inputs['company']['fixture_key'], inputs['role_id']
    companies_path, news_path = directory / 'companies.csv', directory / 'news.sqlite3'
    with companies_path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=profile_spec['columns'], delimiter=';', lineterminator='\n')
        writer.writeheader()
        writer.writerows(profile_spec['rows'])
    news = NewsRepository(news_path)
    items = []
    for candidate in inputs['event_candidates']:
        title, text = candidate['title'], candidate['text']
        source_id = 'cbr:' + hashlib.sha256(candidate['candidate_key'].encode('utf-8')).hexdigest()
        content_hash = hashlib.sha256((title + '\n' + text).encode('utf-8')).hexdigest()
        identity = json.dumps([source_id, content_hash, title, text, None, candidate['published_at']],
                              ensure_ascii=False, separators=(',', ':'))
        event_id = 'news:' + hashlib.sha256(identity.encode('utf-8')).hexdigest()
        if event_id != candidate['event_id']:
            raise ValueError('Input event identity mismatch')
        items.append(SourceItem(source_id, title, None, event_id, candidate['published_at'],
            candidate['published_at'], text, content_hash, 'synthetic_fixture', True))
    news.ingest(items, retrieved_at='2026-10-22T00:00:00Z')
    records = news.latest(limit=10)
    config = MVPConfig(companies_path=companies_path, news_path=news_path, local_access=True)
    app = make_app(directory / 'app.sqlite3', demo_mode=False, analysis_engine='mvp_offline', mvp_config=config)
    with TestClient(app) as client:
        health = client.get('/api/v1/health')
        profiles = client.get('/api/v1/companies')
        if profiles.status_code != 200:
            raise ValueError('Profile configuration rejected')
        profile = next(item for item in profiles.json()['items'] if item['company_id'] == company)
        category = {'TRADE': 'trade', 'TRANSPORT': 'transport_logistics'}.get(profile['category'], profile['category'])
        if category != case['category']:
            raise ValueError('Profile category mismatch')
        response = client.post(f'/api/v1/companies/{company}/analysis-runs',
                               json={'role_id': role, 'idempotency_key': 'evaluation'})
        if response.status_code != 202:
            raise ValueError('Analysis HTTP rejected')
        run = response.json()
        feed = client.get(f'/api/v1/companies/{company}/feed', params={'role_id': role})
        if feed.status_code != 200:
            raise ValueError('Feed HTTP rejected')
        cards = []
        for item in feed.json()['items']:
            response = client.get('/api/v1/insights/' + item['insight_id'])
            if response.status_code != 200:
                raise ValueError('Card HTTP rejected')
            cards.append(response.json())
        return {'status': run['status'], 'execution_mode': run['execution_mode'],
                'health_status': health.status_code, 'cards': cards,
                'source_flags': [{'synthetic': r.synthetic,
                                  'provenance': [s.get('provenance') for s in r.source_refs]} for r in records]}


def predict_provenance(case):
    from backend.analysis.pipeline import AnalysisInput, AnalysisPipeline, Evidence
    from backend.mvp.engine import NoExtraTools

    inputs = case['input']

    class FixedCandidate:
        calls = 0

        def generate(self, context, correction=None):
            self.calls += 1
            return inputs['candidate']

    model = FixedCandidate()
    result = AnalysisPipeline(model, NoExtraTools()).run(AnalysisInput(
        inputs['company']['company_id'], inputs['role_id'], inputs['event']['event_id'],
        tuple(Evidence(**item) for item in inputs['evidence']), ('synthetic',)))
    return {'status': result.status, 'validated': result.validated,
            'reasons': result.reasons, 'offline_model_calls': model.calls}


def predict(output, manifest_path):
    manifest = frozen(manifest_path)
    cases = read(REFERENCE / 'cases.json')
    # This process deliberately never parses labels.json or oracle/protocol text.
    if cases['dataset_id'] != manifest['dataset_id'] or len(cases['cases']) != manifest['case_count']:
        raise ValueError('Dataset identity/count mismatch')
    if (output / 'predictions.json').exists():
        raise ValueError('Prediction output already exists; preserve the previous run')
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    os.environ.update(ALPHA_ANALYSIS_ENGINE='deterministic_demo', ALPHA_DEMO_MODE='0',
        ALPHA_MVP_ALLOW_LIVE='0', ALPHA_MVP_ALLOW_CLIENT_DATA='0', ALPHA_DB_PATH=str(output / 'import.sqlite3'))
    from backend.model_clients import BrokerModelClient
    started = datetime.now(timezone.utc).isoformat()
    before = product_hashes()
    rows = []
    with patch.object(BrokerModelClient, 'live', side_effect=AssertionError('Real broker forbidden')):
        for index, case in enumerate(cases['cases']):
            row = {k: case[k] for k in ('case_id', 'suite', 'category', 'role_id')}
            tick = time.perf_counter()
            try:
                if case['suite'] == 'full_workflow':
                    directory = output / f'fixture-{index:02d}'
                    directory.mkdir()
                    row['actual'] = predict_workflow(case, cases['profile_fixture'], directory)
                elif case['suite'] == 'provenance':
                    row['actual'] = predict_provenance(case)
                else:
                    raise ValueError('Unknown suite')
            except Exception as exc:
                row['error'] = {'type': type(exc).__name__, 'message': str(exc)[:200]}
            row['elapsed_ms'] = round((time.perf_counter() - tick) * 1000, 3)
            rows.append(row)
    if product_hashes() != before:
        raise ValueError('Product changed during prediction')
    frozen(manifest_path)
    save(output / 'predictions.json', {'dataset_id': cases['dataset_id'], 'synthetic': True,
        'mode': 'mvp_offline', 'live_calls': 0, 'answer_key_loaded': False,
        'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(), 'cases': rows})
    save(output / 'prediction-manifest.json', {'predictions_sha256': sha(output / 'predictions.json'),
        'evaluator_sha256': sha(__file__), 'reference_manifest_sha256': sha(manifest_path),
        'product_sha256': before, 'frozen_files': manifest['files']})
    print(json.dumps({'predictions': len(rows), 'errors': sum('error' in x for x in rows),
                      'answer_key_loaded': False, 'live_calls': 0}))


def topic_supported(topic, actual):
    # Conservative surface support for the frozen editorial topic. This is a
    # reproducible aid to personal semantic review, not an LLM quality judge.
    ignored = {'при', 'для', 'или', 'как', 'это', 'может', 'могут', 'потребность'}
    terms = {word[:5] for word in re.findall('[а-яёa-z]+', topic.lower().replace('ё', 'е'))
             if len(word) >= 4 and word not in ignored}
    text = actual.lower().replace('ё', 'е')
    return bool(terms) and sum(term in text for term in terms) / len(terms) >= 0.8


def score(output, manifest_path, semantic_path=None):
    frozen_manifest = frozen(manifest_path)
    proof = read(output / 'prediction-manifest.json')
    if (sha(output / 'predictions.json') != proof['predictions_sha256']
            or sha(__file__) != proof['evaluator_sha256']
            or sha(manifest_path) != proof['reference_manifest_sha256']):
        raise ValueError('Prediction/evaluator/freeze proof changed before scoring')
    predictions = read(output / 'predictions.json')
    labels = read(REFERENCE / 'labels.json')
    if labels['dataset_id'] != predictions['dataset_id'] or labels['dataset_id'] != frozen_manifest['dataset_id']:
        raise ValueError('Labels identity mismatch')
    observed = {item['case_id']: item for item in predictions['cases']}
    if len(observed) != len(predictions['cases']):
        raise ValueError('Duplicate prediction ID')
    metrics = dict(coverage=0, ordering=0, topics=0, unknowns=0, bank=0, synthetic_news=0,
                   false_confirmed=0, provenance=0, role_contrasts=0, profile_contrasts=0)
    semantic = read(semantic_path) if semantic_path else None
    if semantic is not None:
        if semantic.get('predictions_sha256') != proof['predictions_sha256']:
            raise ValueError('Semantic review belongs to different predictions')
        workflow_ids = {key for key, row in observed.items() if row['suite'] == 'full_workflow'}
        if set(semantic.get('cases', {})) != workflow_ids:
            raise ValueError('Semantic review must cover every workflow')
    lexical_topics = 0
    findings, case_results, topics = [], {}, {}
    for label in labels['labels']:
        key, expected = label['case_id'], label['expected']
        row = observed.get(key, {})
        actual = row.get('actual')
        if actual is None:
            findings.append({'case_id': key, 'error': row.get('error', 'missing')})
            continue
        metrics['coverage'] += 1
        if row['suite'] == 'full_workflow':
            cards = actual['cards']
            ordering = [card['event_id'] for card in cards]
            order_ok = actual['status'] == 'succeeded' and ordering == expected['selection_order']
            metrics['ordering'] += order_ok
            text = ' '.join(block['text'] for block in cards[0]['blocks'] if block['kind'] == 'ai_inference') if cards else ''
            lexical_topic_ok = bool(cards) and ordering[0] == expected['selected_event_id'] and all(
                topic_supported(topic, text) for topic in expected['role_relevant_topics'])
            lexical_topics += lexical_topic_ok
            topic_ok = lexical_topic_ok
            if semantic is not None:
                decision = semantic['cases'][key]
                if (type(decision.get('match')) is not bool or not decision.get('rationale')
                        or decision.get('interpretation_sha256') != hashlib.sha256(text.encode('utf-8')).hexdigest()):
                    raise ValueError('Unbound or incomplete semantic judgement')
                topic_ok = bool(cards) and ordering[0] == expected['selected_event_id'] and decision['match']
            metrics['topics'] += topic_ok
            if topic_ok:
                topics[(row['category'], row['role_id'])] = True
            uncertainties = ' '.join(' '.join(card['unknowns']) + ' ' +
                ' '.join(block['text'] for block in card['blocks']) for card in cards).lower()
            unknown_ok = bool(cards) and all(card['exposure_status'] == 'unknown' for card in cards) and (
                any(stem in uncertainties for stem in ('экспозиц', 'связь')) and
                any(stem in uncertainties for stem in ('масштаб', 'финансовый эффект', 'величина эффекта')))
            metrics['unknowns'] += unknown_ok
            metrics['false_confirmed'] += sum(card['exposure_status'] == 'confirmed' for card in cards)
            bank_ok = bool(cards) and all(card['bank_position_status'] == expected['bank_position'] and
                not any(block['kind'] == 'bank_position' for block in card['blocks']) for card in cards)
            metrics['bank'] += bank_ok
            synthetic_ok = bool(actual['source_flags']) and all(flag['synthetic'] and
                flag['provenance'] and all(p == 'synthetic_fixture' for p in flag['provenance']) for flag in actual['source_flags'])
            metrics['synthetic_news'] += synthetic_ok
            case_results[key] = {'ordering_ok': order_ok, 'topic_ok': topic_ok, 'unknown_ok': unknown_ok,
                'bank_ok': bank_ok, 'synthetic_ok': synthetic_ok, 'expected_order': expected['selection_order'],
                'actual_order': ordering, 'expected_topic': expected['role_relevant_topics'], 'actual_interpretation': text}
            if not all((order_ok, topic_ok, unknown_ok, bank_ok, synthetic_ok)):
                findings.append({'case_id': key, 'criteria': [name for name, ok in (
                    ('ordering', order_ok), ('topic', topic_ok), ('unknowns', unknown_ok),
                    ('bank', bank_ok), ('synthetic', synthetic_ok)) if not ok]})
        else:
            valid = actual['validated']
            correct = (valid is not None) == expected['validated_candidate']
            if expected.get('required_exposure_status'):
                correct = correct and valid is not None and valid['exposure_status'] == expected['required_exposure_status']
            if expected.get('required_kind'):
                correct = correct and valid is not None and valid['kind'] == expected['required_kind']
            metrics['provenance'] += correct
            case_results[key] = {'predicate_ok': correct, 'actual_validated': valid is not None}
            if not correct:
                findings.append({'case_id': key, 'criteria': ['provenance']})
    for kind, groups in labels['contrast_groups'].items():
        for group in groups:
            pair = [case_results.get(key, {}) for key in group['case_ids']]
            good = len(pair) == 2 and all(p.get('ordering_ok') and p.get('topic_ok') for p in pair)
            metrics[kind + '_contrasts'] += good
    workflow_count = sum(row['suite'] == 'full_workflow' for row in observed.values())
    provenance_count = sum(row['suite'] == 'provenance' for row in observed.values())
    required = {'coverage': frozen_manifest['case_count'], 'ordering': workflow_count,
                'unknowns': workflow_count, 'bank': workflow_count, 'synthetic_news': workflow_count,
                'false_confirmed': 0, 'provenance': provenance_count,
                'role_contrasts': len(labels['contrast_groups']['role']),
                'profile_contrasts': len(labels['contrast_groups']['profile'])}
    hit_rate = metrics['topics'] / workflow_count if workflow_count else 0
    passed = all(metrics[k] == value for k, value in required.items()) and hit_rate >= 0.75 and len(topics) == 4
    result = {'status': 'passed' if passed else 'failed', 'metrics': metrics, 'required_exact': required,
        'topic_hit_rate': hit_rate, 'topic_required': 0.75, 'category_role_cells_with_hit': len(topics),
        'cases': case_results, 'findings': findings, 'live_calls': 0, 'dataset_id': labels['dataset_id'],
        'predictions_sha256': proof['predictions_sha256'], 'product_sha256': proof['product_sha256'],
        'topic_method': 'Personal semantic comparison to frozen oracle' if semantic else 'Conservative lexical support; personal semantic review still required',
        'lexical_topic_hits': lexical_topics, 'semantic_review_applied': semantic is not None,
        'semantic_review_sha256': sha(semantic_path) if semantic_path else None,
        'limitations': frozen_manifest['limitations'] + [
            'Offline fake model; not real-model or customer usefulness.',
            'After any product repair this set is a frozen regression, not new unseen evaluation.']}
    save(output / 'scores.json', result)
    print(json.dumps({'status': result['status'], 'metrics': metrics, 'topic_hit_rate': result['topic_hit_rate']}, ensure_ascii=False))
    return 0 if passed else 1


def main():
    global REFERENCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('predict', 'score'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--reference-dir', type=Path, default=REFERENCE)
    parser.add_argument('--semantic-review', type=Path, help='Explicit personal semantic judgements, bound to prediction/text hashes')
    args = parser.parse_args()
    REFERENCE = args.reference_dir.resolve()
    REFERENCE.relative_to(ROOT)
    output = args.output.resolve()
    output.relative_to(ROOT / '.tmp')
    if args.mode == 'predict':
        predict(output, args.manifest)
        return 0
    return score(output, args.manifest, args.semantic_review)


if __name__ == '__main__':
    raise SystemExit(main())
