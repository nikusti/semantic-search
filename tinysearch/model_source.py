"""Validated Hugging Face sources for the installed EmbeddingGemma 2 pipeline."""
import hashlib
import json
from pathlib import Path
import re
import threading
from urllib.parse import unquote, urlparse

_registry_lock = threading.Lock()


def parse_url(url):
    parsed = urlparse(url.strip())
    parts = [unquote(p) for p in parsed.path.strip('/').split('/')]
    if (parsed.scheme != 'https' or parsed.netloc != 'huggingface.co' or
            parsed.query or parsed.fragment or len(parts) not in {2, 4} or
            not all(re.fullmatch(r'[A-Za-z0-9_.-]+', p) and p not in {'.', '..'} for p in parts) or
            (len(parts) == 4 and parts[2] != 'tree')):
        raise ValueError('Use https://huggingface.co/owner/model or /owner/model/tree/revision')
    repo = '/'.join(parts[:2])
    revision = parts[3] if len(parts) == 4 else 'main'
    canonical = f'https://huggingface.co/{repo}' + (f'/tree/{revision}' if len(parts) == 4 else '')
    return repo, revision, canonical


def validate_config(values):
    text = values.get('text_config') or {}
    if (values.get('model_type') != 'embedding_gemma2' or
            text.get('model_type') != 'embedding_gemma2_text' or
            text.get('embedding_dim') != 768 or text.get('hidden_size') != 512):
        raise ValueError('Supported: EmbeddingGemma 2 MLX embeddings (768 output dimensions), not chat LLMs or other embedding architectures')
    quant = values.get('quantization')
    if quant is not None and (quant.get('bits') not in {4, 8} or
                               quant.get('mode', 'affine') != 'affine' or quant.get('group_size') != 64):
        raise ValueError('Supported MLX precision: BF16, affine 4-bit or 8-bit with group size 64')
    if quant is None and values.get('dtype') != 'bfloat16':
        raise ValueError('Unquantized models must use BF16')
    vision = values.get('vision_config')
    audio = values.get('audio_config')
    if vision and vision.get('model_type') != 'gemma4_vision':
        raise ValueError('Unsupported vision encoder')
    if audio and audio.get('model_type') != 'gemma4_audio':
        raise ValueError('Unsupported audio encoder')
    return {'images': bool(vision), 'video': bool(vision), 'audio': bool(audio),
            'precision': f"{quant['bits']}-bit" if quant else 'BF16'}


def resolve_source(root, catalog, variant, url=''):
    if variant != 'custom':
        entry = next((m for m in catalog if m['id'] == variant), None)
        if not entry:
            raise ValueError('Unknown model')
        entry = dict(entry)
        if entry['backend'] == 'coreml':
            from .coreml_assets import PIPELINE_REVISION
            entry['pipeline_revision'] = PIPELINE_REVISION
            entry['storage_key'] = PIPELINE_REVISION[:12]
            entry['path'] += '-' + entry['storage_key']
        elif re.fullmatch('[a-f0-9]{40}', entry['revision']) and entry['revision'] != entry.get('legacy_revision'):
            entry['storage_key'] = hashlib.sha256((entry['repo']+'@'+entry['revision']).encode()).hexdigest()[:12]
            entry['path'] += '-' + entry['storage_key']
        return entry
    repo, requested, canonical = parse_url(url)
    # The recommended URL selects the already pinned and downloaded model.
    for entry in catalog:
        if entry['repo'] == repo and (requested == 'main' or requested == entry['revision']):
            return resolve_source(root, catalog, entry['id'])
    registry_path = root / 'models' / 'custom-models.json'
    with _registry_lock:
        registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
        if canonical in registry:
            return registry[canonical]
    from huggingface_hub import HfApi, hf_hub_download
    try:
        info = HfApi().model_info(repo, revision=requested, files_metadata=True, timeout=15)
        config_path = hf_hub_download(repo, 'config.json', revision=info.sha)
        capabilities = validate_config(json.loads(Path(config_path).read_text()))
        # A MLX conversion is required, rather than the original PyTorch weights.
        if info.library_name not in {'mlx', 'mlx-vlm'}:
            raise ValueError('Choose an MLX conversion of EmbeddingGemma 2; the repository must declare the mlx or mlx-vlm library')
        names = {s.rfilename for s in info.siblings}
        if 'tokenizer.json' not in names:
            raise ValueError('Repository is missing tokenizer.json')
        weights = {s.rfilename: s.size for s in info.siblings
                   if re.fullmatch(r'(model|model-[0-9]+-of-[0-9]+)\.safetensors', s.rfilename)}
        if not weights or any(not size for size in weights.values()):
            raise ValueError('Repository is missing MLX model safetensors')
        key = hashlib.sha256(f'{repo}@{info.sha}'.encode()).hexdigest()[:16]
        entry = {'id': 'custom', 'storage_key': key, 'label': f'EmbeddingGemma 2 · MLX {capabilities["precision"]}',
                 'repo': repo, 'revision': info.sha, 'url': f'https://huggingface.co/{repo}/tree/{info.sha}', 'backend': 'mlx',
                 'path': f'models/custom-{key}', 'weights': weights, 'capabilities': capabilities,
                 'download_bytes': sum(weights.values())}
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f'Could not verify Hugging Face model: {error}') from error
    with _registry_lock:
        registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
        registry[canonical] = entry
        registry[entry['url']] = entry
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        temp = registry_path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(registry, ensure_ascii=False, indent=2))
        temp.replace(registry_path)
    return entry


def installed(root, entry):
    path = root / entry['path']
    if entry['backend'] == 'coreml':
        from .coreml_assets import ready
        return ready(path) and all((path/name).is_file() for name in ('embeddings.bf16', 'tokenizer.json', 'processor/config.json'))
    if not all((path / name).is_file() for name in ('config.json', 'tokenizer.json')):
        return False
    if entry['id'] != 'custom':
        return (path / 'model.safetensors').is_file()
    return (path / '.validated').is_file() and all(
        (path / name).is_file() and (path / name).stat().st_size == size
        for name, size in entry['weights'].items())


def ensure_downloaded(root, entry):
    if entry['backend'] == 'coreml':
        from .coreml_assets import ensure_assets
        ensure_assets(path=root/entry['path'], groups=['text'])
        return
    if installed(root, entry):
        return
    import os
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    from huggingface_hub import snapshot_download
    path = root / entry['path']
    patterns = ['*.json', '*.model', '*.jinja', '1_Pooling/*', '2_Normalize/*']
    patterns += list(entry['weights']) if entry['id'] == 'custom' else ['*.safetensors']
    snapshot_download(repo_id=entry['repo'], revision=entry['revision'], local_dir=path,
                      allow_patterns=patterns)
    if entry['id'] == 'custom':
        from safetensors import safe_open
        validate_config(json.loads((path / 'config.json').read_text()))
        keys = set()
        for name, size in entry['weights'].items():
            if not (path / name).is_file() or (path / name).stat().st_size != size:
                raise RuntimeError('Incomplete model download')
            with safe_open(path / name, framework='numpy') as weights:
                keys.update(k.removeprefix('model.') for k in weights.keys())
        if not any(k.startswith('language_model.') for k in keys):
            raise ValueError('Weights are not in the supported MLX EmbeddingGemma 2 format')
        for capability, prefix in (('images', 'vision_tower.'), ('audio', 'audio_tower.')):
            if entry['capabilities'][capability] and not any(k.startswith(prefix) for k in keys):
                raise ValueError(f'Missing {capability} model weights')
        (path / '.validated').write_text(entry['revision'])
    if not installed(root, entry):
        raise RuntimeError('Incomplete model download')
