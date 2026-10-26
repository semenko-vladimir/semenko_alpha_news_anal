"""Bounded hourly refresh of reviewed official sources; no model calls."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3

from .repository import NewsRepository,NewsService
from backend.sources.official_news import refresh_source


def update_news(database,registry,stop=None):
    data=json.loads(Path(registry).read_text(encoding='utf-8'))
    sources=data.get('sources',[])
    if data.get('schema_version')!=1 or not 1<=len(sources)<=16:raise ValueError('news_registry_invalid')
    repo=NewsRepository(database)
    results=[]
    # One bounded CBR fetch, without retries. Other publishers have independent last-good state.
    cbr=NewsService(repo).fetch_and_ingest()
    results.append({'source_id':'cbr','status':cbr['status'],'error':cbr.get('error'),'received':cbr.get('received',0),'inserted':cbr.get('inserted',0)})
    def run(source):
        if stop and stop.is_set():return {'source_id':source['id'],'status':'cancelled'}
        return refresh_source(repo,source)
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs=[pool.submit(run,source) for source in sources]
        for future in as_completed(jobs):results.append(future.result())
    return {'updated_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
            'status':'ok' if all(r['status']=='ok' for r in results) else 'partial',
            'sources':results,'new_model_calls':0}


def product_data_status(database):
    base=NewsRepository.status_snapshot(database)
    uri=Path(database).resolve().as_uri()+'?mode=ro'
    with closing(sqlite3.connect(uri,uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        table=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_official_refresh'").fetchone()
        rows=[dict(r) for r in conn.execute('SELECT * FROM news_official_refresh ORDER BY source_id')] if table else []
    official=[{k:v for k,v in r.items() if k!='source_url'} for r in rows]
    successes=[r['last_success_at'] for r in rows if r.get('last_success_at')]
    if base.get('last_success_at'):successes.append(base['last_success_at'])
    return {'news_count':base['news_count'],'publication_age':base['publication_age'],
        'latest_successful_fetch':max(successes) if successes else None,
        'cbr':base,'official_sources':official,
        'source_errors':sum(r['status']!='ok' for r in rows)+(1 if base.get('last_attempt') and base['last_attempt']['status']!='ok' else 0)}
