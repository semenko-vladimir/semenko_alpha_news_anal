"""Release application factory with persistent inputs and deterministic news refresh."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import threading


def build_product_app(runtime,public_catalog,case_csv=None,bank=None,*,live=False,auto_refresh=False,
                      registry=None,broker_socket=None,broker_status=None,local_model=None,
                      local_endpoint='http://127.0.0.1:11435',local_transport=None):
    if local_model and (live or case_csv or bank):raise ValueError('Local mode is separate and public-only')
    runtime=Path(runtime);runtime.mkdir(parents=True,exist_ok=True)
    from backend.news_service import NewsRepository
    from backend.news_service.product_refresh import update_news
    from backend.model_clients.local_bridge import LocalBrokerTransport
    news=runtime/'news.sqlite3';NewsRepository(news)
    # Avoid import-time selection of a live engine. Operator config is passed explicitly below.
    os.environ['ALPHA_ANALYSIS_ENGINE']='deterministic_demo';os.environ['ALPHA_DEMO_MODE']='0'
    # backend.app also constructs its compatibility ASGI app at import time.
    # Keep that initialization inside the writable product volume on read-only containers.
    os.environ['ALPHA_DB_PATH']=str(runtime/'app.sqlite3')
    from backend.app import make_app
    from backend.mvp.config import MVPConfig
    transport=LocalBrokerTransport(broker_socket) if broker_socket else None
    status=transport.status() if live and transport else broker_status
    if live and not status:raise ValueError('A safe broker status snapshot is required before live startup')
    if live and (status.get('daily_attempt_limit')!=4 or status.get('max_output_tokens')!=512):raise ValueError('Broker policy mismatch')
    from backend.model_clients.ollama_local import LocalOllamaClient
    local=LocalOllamaClient(local_endpoint,model=local_model,transport=local_transport,prompt_version='structured-v2') if local_model else None
    config=MVPConfig(companies_path=case_csv,news_path=news,bank_path=bank,public_catalog_path=public_catalog,
        local_access=True,allow_live=live,allow_client_data=live,
        task_id='LOCAL-LLM-001-preview' if local else 'FINAL-PRODUCT-001' if live else None,
        operation_jobs=bool(live or local),transport=transport,local_model_client=local)
    app=make_app(runtime/'app.sqlite3',analysis_engine='local_ollama' if local else 'broker_live' if live else 'mvp_offline',mvp_config=config)
    if app.state.mvp_engine.blocker:raise ValueError('Product inputs rejected: '+app.state.mvp_engine.blocker)
    if live:
        today=datetime.now(timezone.utc).date().isoformat()
        if status.get('budget_day')==today and status.get('remaining_attempts')==0:
            app.state.operation_jobs.stop('budget_exceeded')
    prior=app.router.lifespan_context
    stop=threading.Event()
    def refresh_loop():
        while not stop.is_set():
            try:
                result=update_news(news,registry,stop=stop)
                temporary=runtime/'refresh-result.next.json';temporary.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
                temporary.replace(runtime/'refresh-result.json')
            except Exception as exc:
                (runtime/'refresh-error.json').write_text(json.dumps({'error':type(exc).__name__}),encoding='utf-8')
            if stop.wait(3600):break
    @asynccontextmanager
    async def lifespan(application):
        async with prior(application):
            worker=None
            if auto_refresh:
                if not registry:raise ValueError('Official news registry is required')
                worker=threading.Thread(target=refresh_loop,name='official-news-refresh',daemon=True);worker.start()
            try:yield
            finally:
                stop.set()
                if worker:await asyncio.to_thread(worker.join,45)
    app.router.lifespan_context=lifespan
    return app


def main():
    # Named non-secret operator settings only. Never enumerate the environment.
    runtime=Path(os.getenv('ALPHA_PRODUCT_RUNTIME','/var/lib/alpha-product'))
    app=build_product_app(runtime,os.getenv('ALPHA_PUBLIC_CATALOG_PATH','/app/data/catalog/companies.json'),
        case_csv=os.getenv('ALPHA_MVP_COMPANIES_PATH') or None,bank=os.getenv('ALPHA_MVP_BANK_PATH') or None,
        live=os.getenv('ALPHA_PRODUCT_LIVE')=='1',auto_refresh=os.getenv('ALPHA_PRODUCT_AUTO_REFRESH','1')=='1',
        registry=os.getenv('ALPHA_NEWS_REGISTRY','/app/data/catalog/news_sources.json'),broker_socket=os.getenv('ALPHA_BROKER_SOCKET_PATH'),
        local_model=os.getenv('ALPHA_PRODUCT_LOCAL_MODEL') or None,local_endpoint=os.getenv('ALPHA_OLLAMA_LOCAL_URL','http://127.0.0.1:11435'))
    import uvicorn
    uvicorn.run(app,host='0.0.0.0',port=8000,access_log=False)


if __name__=='__main__':main()
