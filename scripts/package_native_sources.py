"""Assemble complete native dependency sources beside the installation assets."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
def main():
    manifest=json.loads((ROOT/'packaging/native-sources.json').read_text())
    folder=ROOT/'.build/native-sources';folder.mkdir(parents=True,exist_ok=True)
    def fetch(entry):
        path=folder/entry['name'];path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            local=ROOT/'.build/lgpl-codecs'/entry['name']
            if local.is_file():shutil.copyfile(local,path)
            else:urllib.request.urlretrieve(entry['url'],path)
        if sha256(path.read_bytes()).hexdigest()!=entry['sha256']: raise RuntimeError('Source checksum mismatch: '+entry['name'])
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(fetch,manifest))
    shutil.copyfile(ROOT/'packaging/native-sources.json',folder/'manifest.json')
    shutil.copyfile(ROOT/'scripts/build_lgpl_codecs.py',folder/'build_lgpl_codecs.py')
    shutil.copyfile(ROOT/'LICENSE',folder/'LICENSE')
    shutil.rmtree(folder/'NOTICES',ignore_errors=True)
    shutil.copytree(ROOT/'packaging/codec-notices',folder/'NOTICES')
    version=json.loads((ROOT/'extension/package.json').read_text())['version']
    output=ROOT.parent/f'semantic-search-native-sources-v{version}.zip'
    members={entry['name'] for entry in manifest}|{'manifest.json','build_lgpl_codecs.py','LICENSE'}
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob('*')):
            name=path.relative_to(folder).as_posix()
            if path.is_file() and (name in members or name.startswith('NOTICES/')):
                archive.write(path,name,compress_type=zipfile.ZIP_STORED if '.tar.' in path.name else zipfile.ZIP_DEFLATED)
    print(output.name,sha256(output.read_bytes()).hexdigest())
if __name__=='__main__':main()
