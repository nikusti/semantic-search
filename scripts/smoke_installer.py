"""Maintainer test: a downloaded installer, fresh storage, no external runtime tools.

Only synthetic fixtures are indexed. Optional model copies avoid another large
download; the test never indexes or edits the source library/model directory.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import socket
import time
import urllib.error
import urllib.request


FIXTURES = r'''
from pathlib import Path
import sys
from PIL import Image, ImageDraw
root = Path(sys.argv[1]); root.mkdir(parents=True)
(root / 'migration-checklist.md').write_text('Data migration checklist: back up the database, validate row counts and test rollback before moving to a new server.')
for name, color, shape in [('orange-circle.png', 'orange', 'ellipse'), ('blue-square.png', 'blue', 'rectangle')]:
    image = Image.new('RGB', (640, 420), 'white')
    getattr(ImageDraw.Draw(image), shape)((160, 70, 480, 370), fill=color)
    destination = root / 'blue-folder' / name if name.startswith('blue-') else root / name
    destination.parent.mkdir(exist_ok=True)
    image.save(destination)
text = b'BT /F1 12 Tf 50 750 Td (Car maintenance: change engine oil, inspect brakes and check tires.) Tj ET'
objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
 b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
 b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', b'<< /Length ' + str(len(text)).encode() + b' >>\nstream\n' + text + b'\nendstream']
data = bytearray(b'%PDF-1.4\n'); offsets = [0]
for index, obj in enumerate(objects, 1):
 offsets.append(len(data)); data += str(index).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
xref = len(data); data += b'xref\n0 6\n0000000000 65535 f \n'
for offset in offsets[1:]: data += f'{offset:010d} 00000 n \n'.encode()
data += b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n'
(root / 'maintenance.pdf').write_bytes(data)
excluded = root / 'excluded'; excluded.mkdir()
for index in range(16): (excluded / f'sample-{index}.txt').write_text('A synthetic excluded library record.')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True, help='A new, empty test directory')
    parser.add_argument('--models-copy-from', type=Path, help='Already downloaded built-in models; copied, never modified')
    parser.add_argument('--port', type=int, default=18791)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535: raise SystemExit('Use an unoccupied local test port')
    with socket.socket() as probe:
        try: probe.bind(('127.0.0.1', args.port))
        except OSError: raise SystemExit('Test port is occupied; no existing service will be touched')
    work = args.work.resolve()
    if work.exists(): raise SystemExit('Use a new test directory, not an existing library')
    work.mkdir(parents=True)
    env = {**os.environ, 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
           'HF_HUB_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
           'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    extracted = work / 'extracted'
    subprocess.run(['/usr/bin/ditto', '-x', '-k', str(args.installer.resolve()), str(extracted)], check=True, env=env)
    app = extracted / 'Semantic Search Engine Installer.app'
    manifest = json.loads((app / 'Contents/Resources/runtime-manifest.json').read_text())
    storage = work / 'storage'
    for name in ('data', 'models'):
        (storage / name).mkdir(parents=True)
        (storage / name / 'sentinel').write_text('preserve')
    installer = app / 'Contents/MacOS/semantic-search-installer'
    command = [str(installer), '--install-test', '--storage', str(storage)]
    report = {'version': manifest['version'], 'minimal_path': True, 'model_source': 'local copies' if args.models_copy_from else 'none'}
    for label in ('fresh_install', 'repeat_install'):
        started = time.monotonic()
        subprocess.run(command, check=True, env=env)
        report[label + '_seconds'] = round(time.monotonic() - started, 2)
        for name in ('data', 'models'): assert (storage / name / 'sentinel').read_text() == 'preserve'
    runtime = storage / 'runtime' / manifest['version']
    assert json.loads((runtime / 'installed.json').read_text()) == manifest
    assert not (runtime.parent / 'install.lock').exists()
    assert not list(runtime.parent.glob('.install-*'))
    python = runtime / 'python/bin/python3.12'
    fixtures = work / 'fixtures'
    subprocess.run([str(python), '-I', '-B', '-c', FIXTURES, str(fixtures)], check=True, env=env)
    if args.models_copy_from:
        for name in ('embeddinggemma-2-mlx-8bit', 'embeddinggemma-2-coreml-a4c2764a75bd'):
            source = args.models_copy_from.resolve() / name
            if source.is_dir():
                if source.is_symlink() or any(path.is_symlink() for path in source.rglob('*')):
                    raise SystemExit('Model test sources must contain regular files/directories, not symlinks')
                # macOS APFS clones are independent copies and avoid duplicating
                # gigabytes. Changes/deletion in the test cannot edit originals.
                subprocess.run(['/bin/cp', '-cR', str(source), str(storage / 'models' / name)], check=True)
    if args.models_copy_from:
        preflight = """
from hashlib import sha256
from pathlib import Path
import sys
from tinysearch.coreml_assets import MANIFEST, ready
path = Path(sys.argv[1])
assert all(ready(path, group) for group in ('text', 'vision')), 'Core ML text/vision copies must be complete'
for name, entry in MANIFEST['processor']['files'].items():
    asset = path / 'processor' / name
    assert asset.is_file() and asset.stat().st_size == entry['bytes'] and sha256(asset.read_bytes()).hexdigest() == entry['sha256'], 'Incomplete Core ML processor copy'
"""
        subprocess.run([str(python), '-I', '-B', '-c', preflight,
                        str(storage / 'models/embeddinggemma-2-coreml-a4c2764a75bd')], check=True, env=env)
    base = f'http://127.0.0.1:{args.port}'
    process = None
    def owned_listener():
        if process is None or process.poll() is not None: return False
        result = subprocess.run(['/usr/sbin/lsof', '-n', '-P', '-iTCP:' + str(args.port), '-sTCP:LISTEN', '-t'],
                                capture_output=True, text=True, env=env)
        return result.returncode == 0 and set(result.stdout.split()) == {str(process.pid)}
    def request(endpoint, body=None):
        if not owned_listener(): raise ConnectionError('The test does not own this listener; no API request will be sent')
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + '/v1/' + endpoint, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=180) as response: return json.load(response)
    def settle():
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            job = request('settings')['job']
            if job['state'] == 'error': raise AssertionError(job['error'])
            if job['state'] in ('complete', 'cancelled', 'deleted') and not request('status')['indexing']: return job
            time.sleep(.15)
        raise TimeoutError('Index/settings operation did not finish')
    log = (work / 'service.log').open('a')
    def start():
        nonlocal process
        with socket.socket() as probe:
            try: probe.bind(('127.0.0.1', args.port))
            except OSError: raise RuntimeError('Test port became occupied; refusing to touch its service')
        process = subprocess.Popen([str(python), '-I', '-B', '-m', 'tinysearch.runtime', '--storage', str(storage), '--port', str(args.port)],
                                   stdout=log, stderr=log, env=env, start_new_session=True)
        for _ in range(100):
            try:
                assert request('info')['version'] == manifest['version']; return
            except (OSError, AssertionError): time.sleep(.1)
        raise RuntimeError('Installed engine failed to start')
    def stop():
        request('engine/stop', {})
        process.wait(timeout=15)
    try:
        start()
        assert request('status')['files'] == 0
        backend_reports = []
        for variant in ('mlx-8bit', 'coreml-experimental'):
            settings = request('settings')
            if not any(model['id'] == variant and model['installed'] for model in settings['models']): continue
            values = settings['current']
            values.update(model_variant=variant, roots=[str(fixtures)], excluded_paths=[str(fixtures / 'excluded')],
                          enable_documents=True, enable_images=True, enable_audio=False, enable_video=False)
            request('settings', values); settle()
            request('index/start', {}); settle()
            assert request('status')['files'] == 4
            elapsed = []
            for query, wanted in [('moving database to a new server', 'migration-checklist.md'), ('an orange circle', 'orange-circle.png')]:
                started = time.monotonic(); results = request('search', {'query': query, 'limit': 8})['results']
                assert results[0]['name'] == wanted, (variant, query, [item['name'] for item in results])
                assert not any('/excluded/' in item['path'] for item in results)
                elapsed.append(round(time.monotonic() - started, 3))
            assert not request('status')['runtime'].get('vision_loaded')
            for name in ('orange-circle.png', 'maintenance.pdf'):
                preview = request('preview', {'path': str(fixtures / name), 'animate': True})
                assert preview['preview'].startswith('data:image/jpeg;base64,')
                if name.endswith('.pdf'): assert preview['pages'] == 1
            before = request('status')['chunks']
            request('index/start', {}); settle()
            assert request('status')['chunks'] == before
            backend_reports.append({'model': variant, 'search_seconds': elapsed, 'files': 4, 'vectors': before})
            print(f'{variant}: text/photo search, PDF/photo previews and unchanged-index reuse passed.', flush=True)
        if args.models_copy_from: assert len(backend_reports) == 2, 'Both built-in model copies must be available'
        report['backends'] = backend_reports
        if backend_reports:
            (fixtures / 'release-notes.txt').write_text('Offline installer release notes and installation instructions.')
            request('index/start', {}); settle(); assert request('status')['files'] == 5
            (fixtures / 'release-notes.txt').unlink()
            (fixtures / 'migration-checklist.md').write_text('Updated data migration checklist: validate checksums before switching to the new database server.')
            request('index/start', {}); settle(); assert request('status')['files'] == 4
            summary = request('status')['last_index_summary']
            assert summary['updated'] == 1 and summary['deleted'] == 1, summary
            # Excluding an already indexed photo removes only its own record.
            request('exclusions', {'excluded_paths': [str(fixtures / 'excluded'), str(fixtures / 'blue-folder')]})
            assert request('status')['files'] == 3
            request('search', {'query': 'data migration', 'limit': 8})
            deadline = time.monotonic() + 85
            while request('status')['runtime'].get('worker_pid'):
                if time.monotonic() > deadline: raise TimeoutError('Idle model did not unload')
                time.sleep(1)
            report['idle_unload'] = True
            count = request('status')['chunks']
            stop(); start()
            assert request('status')['files'] == 3 and request('status')['chunks'] == count
            assert request('search', {'query': 'data migration', 'limit': 8})['results'][0]['name'] == 'migration-checklist.md'
            # Remove only the copied model. Existing vectors and originals stay.
            request('models/delete', {'model_variant': backend_reports[-1]['model']})
            assert request('status')['chunks'] == count
            assert (fixtures / 'orange-circle.png').exists()
            report['restart_preserves_index'] = report['model_delete_preserves_index'] = True
            print('Incremental updates, exclusions, idle unloading, restart and copied-model deletion passed.', flush=True)
        stop(); process = None
        for name in ('data', 'models'): assert (storage / name / 'sentinel').read_text() == 'preserve'
        (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2), flush=True)
    finally:
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=15)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        log.close()


if __name__ == '__main__': main()
