"""Prepare separate product runtimes; optional case subset stays private and untracked."""
import argparse
from contextlib import closing
import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.mvp.config import ALLOWED_COMPANIES
from backend.profiles.repository import ProfileRepository,COMPANY_FIELDS
from backend.news_service import NewsRepository


def sha(file):
    result=hashlib.sha256()
    with file.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case-csv',type=Path)
    parser.add_argument('--news-seed',type=Path)
    parser.add_argument('--bank-index',type=Path)
    parser.add_argument('--port',type=int,default=8089)
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(ROOT/'.tmp') or output==ROOT/'.tmp':parser.error('Output must be a new child of project .tmp')
    if output.exists():parser.error('Existing runtime preserved; use a new output path')
    if not 1<=args.port<=65535:parser.error('Invalid port')
    hashes={};rows=[]
    if args.case_csv:
        # Validate selected source records before preparing a derived four-row subset.
        ProfileRepository(args.case_csv).load(ALLOWED_COMPANIES)
        before=sha(args.case_csv)
        with args.case_csv.open(encoding='utf-8-sig',newline='') as stream:
            for row in csv.DictReader(stream,delimiter=';'):
                if row['SYNTHETIC_U'] in ALLOWED_COMPANIES:rows.append(row)
        if sha(args.case_csv)!=before:raise ValueError('Original case changed during preparation')
        hashes['case_source_sha256']=before
    output.mkdir(parents=True)
    if os.name!='nt':output.chmod(0o700)
    if rows:
        with (output/'case.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=COMPANY_FIELDS,delimiter=';');writer.writeheader();writer.writerows(rows)
    for name in ['runtime-native','runtime-docker']:
        directory=output/name;directory.mkdir()
        target=directory/'news.sqlite3'
        if args.news_seed:
            with closing(sqlite3.connect(args.news_seed.resolve().as_uri()+'?mode=ro',uri=True)) as source:
                with closing(sqlite3.connect(target)) as destination:source.backup(destination)
        else:NewsRepository(target)
        if os.name!='nt':directory.chmod(0o2770);target.chmod(0o660)
    values={'PRODUCT_WEB_PORT':str(args.port),'PRODUCT_RUNTIME_DIR':(output/'runtime-docker').as_posix()}
    values['PRODUCT_DATA_GID']=str(os.getgid() if hasattr(os,'getgid') else 10001)
    if args.bank_index:
        if not args.bank_index.is_file():raise ValueError('Bank index not found')
        values['PRODUCT_BANK_INDEX_FILE']=args.bank_index.resolve().as_posix()
    if rows:values['PRODUCT_CASE_FILE']=(output/'case.csv').as_posix()
    (output/'product.env').write_text('\n'.join(k+'="'+v.replace('"','\\"')+'"' for k,v in values.items())+'\n',encoding='utf-8')
    manifest={'public_catalog':'data/catalog/companies.json','case_profiles':len(rows),'case_origin':'provided' if rows else 'not_connected',
        'originals_unchanged':True,'seed_news':bool(args.news_seed),'contains_no_api_credentials':True,**hashes}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'prepared','public_profiles':11,'case_profiles':len(rows),'new_model_calls':0}))


if __name__=='__main__':main()
