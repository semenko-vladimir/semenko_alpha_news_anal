"""Create a reviewable source bundle from an explicit public allowlist, without Git writes."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
ROOT_FILES={'.dockerignore','.gitignore','.gitattributes','Dockerfile','README.md','THIRD_PARTY_NOTICES.md',
 'compose.yaml','compose.bank.yaml','compose.product.yaml','compose.product.case.yaml',
 'compose.product.bank.yaml','compose.product.live.yaml','compose.product.https.yaml'}
PUBLIC_DOCS={'README.md','setup.md','architecture.md','testing.md','limitations.md','judging.md',
 'quality-monitoring.md','final-product.md','local-llm.md'}
SUFFIXES={'.py','.tsx','.ts','.css','.svg','.json','.html','.cjs','.js','.mjs','.md','.txt','.lock','.sh','.yaml','.yml','.conf','.xml'}
EXCLUDED={'node_modules','__pycache__','dist','.tmp','.venv','.pytest_cache','.git'}
PUBLIC_SCRIPTS={'README.md','prepare_mvp_demo.py','run_mvp.py','verify-agent-chain.py','verify-local-demo.py',
 'verify-local-demo.md','verify-mvp.py','run-local-demo.ps1','stop-local-demo.ps1','model-broker.ps1',
 'prepare_product.py','run_product.py','update_product_data.py','configure_product_access.py',
 'broker_socket_bridge.py','package_product.py','hackathon_broker/README.md',
 'hackathon_broker/broker.py','hackathon_broker/test_broker.py'}


def public_files():
    files=[ROOT/name for name in ROOT_FILES if (ROOT/name).is_file()]
    files += [ROOT/'docs'/name for name in PUBLIC_DOCS if (ROOT/'docs'/name).is_file()]
    files += [ROOT/'data/catalog'/name for name in ['companies.json','news_sources.json']]
    files += [ROOT/'scripts'/name for name in PUBLIC_SCRIPTS if (ROOT/'scripts'/name).is_file()]
    for directory in ['backend','frontend','deploy','tests','examples']:
        for file in (ROOT/directory).rglob('*'):
            relative=file.relative_to(ROOT)
            if any(part in EXCLUDED or part.startswith('.') for part in relative.parts):continue
            if file.is_file() and file.suffix in SUFFIXES:files.append(file)
    result=[]
    for file in sorted(set(files)):
        if file.is_symlink() or not file.resolve().is_relative_to(ROOT):raise ValueError('Symlinks are excluded')
        if file.suffix=='.txt' and file.name not in {'requirements.txt','requirements-dev.txt'}:continue
        result.append(file)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(ROOT/'.tmp') or output.suffix!='.zip':parser.error('Output must be a new ZIP under project .tmp')
    if output.exists():parser.error('Existing release preserved; use a new output filename')
    output.parent.mkdir(parents=True,exist_ok=True)
    manifest={}
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for file in public_files():
            relative=file.relative_to(ROOT).as_posix();content=file.read_bytes()
            manifest[relative]=hashlib.sha256(content).hexdigest();archive.writestr(relative,content)
        archive.writestr('RELEASE-MANIFEST.json',json.dumps({'schema_version':1,'files':manifest,
            'private_inputs_included':False,'live_enabled_by_default':False},indent=2)+'\n')
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.sha256').write_text(digest+'  '+output.name+'\n',encoding='ascii')
    print(json.dumps({'status':'packaged','files':len(manifest),'sha256':digest,'archive':str(output),'private_inputs':False}))


if __name__=='__main__':main()
