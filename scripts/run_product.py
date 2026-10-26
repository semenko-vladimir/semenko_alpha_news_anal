"""Run the final catalog locally. Live mode requires explicit opt-in and safe status."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8011)
    parser.add_argument('--runtime',type=Path,default=ROOT/'.tmp/final-product')
    parser.add_argument('--case-csv',type=Path)
    parser.add_argument('--bank',type=Path)
    route=parser.add_mutually_exclusive_group()
    route.add_argument('--live',action='store_true')
    route.add_argument('--local-ollama',action='store_true')
    parser.add_argument('--local-model',default='hf.co/unsloth/Qwen3.5-4B-GGUF:Q6_K')
    parser.add_argument('--ollama-url',default='http://127.0.0.1:11435')
    parser.add_argument('--broker-status',type=Path)
    parser.add_argument('--broker-host',default='semenkohome')
    parser.add_argument('--broker-script',default='/home/feelwent/.local/share/alpha-hack-broker/broker.py')
    parser.add_argument('--auto-refresh',action='store_true')
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('Invalid port')
    with socket.socket() as probe:probe.bind(('127.0.0.1',args.port))
    if args.live and not args.broker_status:parser.error('--broker-status must point to safe aggregate metadata')
    if args.local_ollama and (args.case_csv or args.bank or args.broker_status):parser.error('Local mode is public-only and separate from the broker')
    status=json.loads(args.broker_status.read_text(encoding='utf-8-sig')) if args.broker_status else None
    os.environ['ALPHA_BROKER_HOST']=args.broker_host;os.environ['ALPHA_BROKER_SCRIPT']=args.broker_script
    from backend.product_server import build_product_app
    app=build_product_app(args.runtime,ROOT/'data/catalog/companies.json',case_csv=args.case_csv,bank=args.bank,
        live=args.live,auto_refresh=args.auto_refresh,registry=ROOT/'data/catalog/news_sources.json',broker_status=status,
        local_model=args.local_model if args.local_ollama else None,local_endpoint=args.ollama_url)
    from fastapi.staticfiles import StaticFiles
    app.mount('/',StaticFiles(directory=ROOT/'frontend/dist',html=True),name='frontend')
    import uvicorn
    print(f'Product: http://127.0.0.1:{args.port}/ | '+('local_ollama' if args.local_ollama else 'broker_live' if args.live else 'OFFLINE TEST MODEL'),flush=True)
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)


if __name__=='__main__':main()
