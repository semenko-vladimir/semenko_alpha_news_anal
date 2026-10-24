"""Operator-only build command; stdout contains metadata, never bank text."""

import argparse
import json

from .repository import build_index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--ocr-dir", required=True)
    parser.add_argument("--pdf-dir", required=True)
    args = parser.parse_args()
    result = build_index(args.index, manifest_path=args.manifest, ocr_dir=args.ocr_dir, pdf_dir=args.pdf_dir)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
