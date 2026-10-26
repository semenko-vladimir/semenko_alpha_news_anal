"""Refresh official news once; does not generate analysis or read any credentials."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.news_service.product_refresh import update_news,product_data_status


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--registry',type=Path,default=ROOT/'data/catalog/news_sources.json')
    parser.add_argument('--status',action='store_true')
    args=parser.parse_args()
    value=product_data_status(args.database) if args.status else update_news(args.database,args.registry)
    print(json.dumps(value,ensure_ascii=False))


if __name__=='__main__':main()
