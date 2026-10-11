from contextlib import nullcontext
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import time

from fastapi.testclient import TestClient
import numpy as np
import pytest
from safetensors.numpy import save_file

from test_store import FakeEngine
from tinysearch.config import Config
from tinysearch.engine import engine_identity
from tinysearch.model_source import parse_url, validate_config, resolve_source, installed
from tinysearch.server import create_app


def metadata():
    return {'model_type':'embedding_gemma2','dtype':'bfloat16',
            'text_config':{'model_type':'embedding_gemma2_text','embedding_dim':768,'hidden_size':512},
            'vision_config':None,'audio_config':None,
            'quantization':{'bits':8,'group_size':64,'mode':'affine'}}


@pytest.mark.parametrize('url', ['http://huggingface.co/a/b', 'https://example.com/a/b',
    'https://huggingface.co@localhost/a/b', 'https://huggingface.co/a/../b',
    'https://huggingface.co/a/b/resolve/main/model.safetensors',
    'https://huggingface.co/a/b?download=true', 'https://huggingface.co/a/b/tree/..'])
def test_reject_invalid_url(url):
    with pytest.raises(ValueError):parse_url(url)


def test_supported_precisions_and_reject_other_architectures():
    for bits in (4,8):
        values=metadata();values['quantization']['bits']=bits
        assert validate_config(values)['precision']==f'{bits}-bit'
    values=metadata();values.pop('quantization')
    assert validate_config(values)['precision']=='BF16'
    for values in ({'model_type':'gemma3'}, {**metadata(),'dtype':'float16','quantization':None},
                   {**metadata(),'quantization':{'bits':2,'group_size':64}}):
        with pytest.raises(ValueError):validate_config(values)


def wait(client):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        data=client.get('/v1/settings').json()
        if data['job']['state'] in {'complete','error','downloaded'}:return data
        time.sleep(.01)
    raise AssertionError(data)


def test_custom_inspect_download_apply_pin_and_failure_preserves_current(tmp_path,monkeypatch):
    import tinysearch.settings as settings
    import huggingface_hub
    class Engine(FakeEngine):
        def __init__(self,config):self.identity=engine_identity(config);self.dimensions=config.dimensions
        def pin(self):return nullcontext()
        def close(self):pass
        def unload(self):pass
    root=tmp_path/'files';root.mkdir();(root/'a.txt').write_text('sample content')
    values=dict(model_variant='custom',model_url='https://huggingface.co/test/conversion',
                dimensions=128,enable_documents=True,enable_images=False,enable_audio=False,
                enable_video=False,thumbnail_px=128,chunk_tokens=256,idle_seconds=60)
    source=tmp_path/'source';source.mkdir()
    (source/'config.json').write_text(json.dumps(metadata()));(source/'tokenizer.json').write_text('{}')
    save_file({'language_model.test.weight':np.ones((2,2),dtype=np.float32)},source/'model.safetensors')
    revision='a'*40;calls=[];downloads=[]
    def info(repo,**kwargs):
        calls.append((repo,kwargs))
        return SimpleNamespace(sha=revision,library_name='mlx',siblings=[
            SimpleNamespace(rfilename=n,size=(source/n).stat().st_size)
            for n in ('config.json','tokenizer.json','model.safetensors')])
    monkeypatch.setattr(huggingface_hub.HfApi,'model_info',lambda self,repo,**kwargs:info(repo,**kwargs))
    monkeypatch.setattr(huggingface_hub,'hf_hub_download',lambda *args,**kwargs:str(source/'config.json'))
    def download(**kwargs):
        import shutil
        downloads.append(kwargs)
        shutil.copytree(source,kwargs['local_dir'],dirs_exist_ok=True)
    monkeypatch.setattr(huggingface_hub,'snapshot_download',download)
    monkeypatch.setattr(settings,'ROOT',tmp_path);monkeypatch.setattr(settings,'APP_SUPPORT',tmp_path);monkeypatch.setattr(settings,'ManagedEngine',Engine)
    builtin=tmp_path/'models/recommended';builtin.mkdir(parents=True)
    for name in ('config.json','tokenizer.json','model.safetensors'):(builtin/name).write_text('test')
    catalog=[{'id':'mlx-8bit','repo':'test/recommended','revision':'pinned','path':'models/recommended','backend':'mlx','label':'Recommended','recommended':True,'download_bytes':4}]
    monkeypatch.setattr(settings,'CATALOG',catalog)
    cfg=Config((root,),builtin,'pinned',tmp_path/'active.sqlite3',backend='mlx',model_variant='mlx-8bit',dimensions=128,source_path=tmp_path/'config.toml')
    cfg.save();original_config=cfg.source_path.read_text()
    with TestClient(create_app(cfg,Engine(cfg))) as client:
        client.post('/v1/index')
        entry=client.post('/v1/models/inspect',json={'model_variant':'custom','model_url':values['model_url']}).json()
        assert not entry['installed'] and entry['revision']==revision
        assert not (tmp_path/entry['path']/'model.safetensors').exists() and not downloads
        pinned=entry['url']
        assert resolve_source(tmp_path,catalog,'custom',pinned)['storage_key']==entry['storage_key'] and len(calls)==1
        assert client.post('/v1/models/download',json=values).status_code==200
        result=wait(client);assert result['job']['state']=='downloaded',result
        assert cfg.source_path.read_text()==original_config
        assert client.get('/v1/status').json()['model']==engine_identity(cfg)
        assert installed(tmp_path,entry) and downloads[0]['revision']==revision
        assert client.post('/v1/settings',json=values).status_code==200
        result=wait(client);assert result['job']['state']=='complete',result
        saved=Config.load(cfg.source_path)
        assert saved.model_variant=='custom' and saved.model_url==pinned and saved.model_revision==revision
        assert saved.database!=cfg.database and client.get('/v1/status').json()['files']==1
        assert len(downloads)==1 and len(calls)==1
        other=resolve_source(tmp_path,catalog,'custom','https://huggingface.co/other/conversion')
        other_config=settings.configured(saved,{**values,'model_url':other['url']})
        assert other_config.database!=saved.database and engine_identity(other_config)!=engine_identity(saved)
        assert client.post('/v1/settings',json={**values,'enable_audio':True}).status_code==422
        previous=cfg.source_path.read_text()
        assert client.post('/v1/settings',json={**values,'model_url':'https://example.com/llm'}).status_code==422
        assert cfg.source_path.read_text()==previous
        class BadEngine(Engine):
            def query(self,text):raise ValueError('Incompatible tensor shapes')
        monkeypatch.setattr(settings,'ManagedEngine',BadEngine)
        assert client.post('/v1/settings',json={**values,'model_url':other['url']}).status_code==200
        result=wait(client);assert result['job']['state']=='error' and 'tensor shapes' in result['job']['error']
        assert cfg.source_path.read_text()==previous and client.get('/v1/status').json()['model']==engine_identity(saved)
        assert not other_config.database.exists()


def test_recommended_url_alias_does_not_download_or_create_new_index(tmp_path,monkeypatch):
    import tinysearch.settings as settings
    catalog=[{'id':'mlx-8bit','repo':'mlx-community/embeddinggemma-2-8bit','revision':'pinned','path':'models/recommended','backend':'mlx'}]
    monkeypatch.setattr(settings,'ROOT',tmp_path);monkeypatch.setattr(settings,'APP_SUPPORT',tmp_path);monkeypatch.setattr(settings,'CATALOG',catalog)
    cfg=Config((tmp_path,),tmp_path/'unused','pinned',tmp_path/'index.sqlite3',backend='mlx',model_variant='mlx-8bit')
    values=dict(model_variant='mlx-8bit',dimensions=512,enable_documents=True,enable_images=True,thumbnail_px=256,chunk_tokens=512,idle_seconds=60)
    builtin=settings.configured(cfg,values)
    custom=settings.configured(cfg,{**values,'model_variant':'custom','model_url':'https://huggingface.co/mlx-community/embeddinggemma-2-8bit'})
    assert builtin.database==custom.database and engine_identity(builtin)==engine_identity(custom)
    assert custom.model_variant=='mlx-8bit' and not custom.model_url


def test_updated_builtin_weights_and_coreml_pipeline_have_isolated_storage(tmp_path,monkeypatch):
    import tinysearch.coreml_assets as assets
    mlx={'id':'mlx-8bit','repo':'test/mlx','revision':'a'*40,'legacy_revision':'a'*40,'path':'models/mlx','backend':'mlx'}
    old=resolve_source(tmp_path,[mlx],'mlx-8bit')
    updated=resolve_source(tmp_path,[{**mlx,'revision':'b'*40}],'mlx-8bit')
    assert old['path']=='models/mlx' and updated['path']!=old['path']
    assert updated['storage_key']
    core={'id':'coreml-experimental','repo':'test/core','revision':'c'*40,'path':'models/core','backend':'coreml'}
    first=resolve_source(tmp_path,[core],core['id'])
    assert resolve_source(tmp_path,[core],'custom','https://huggingface.co/test/core')==first
    monkeypatch.setattr(assets,'PIPELINE_REVISION','d'*64)
    second=resolve_source(tmp_path,[core],core['id'])
    assert second['path']!=first['path'] and second['storage_key']!=first['storage_key']


def test_incompatible_repository_rejected_before_weights(tmp_path,monkeypatch):
    import huggingface_hub
    bad=tmp_path/'config.json';bad.write_text(json.dumps({'model_type':'gemma3'}))
    monkeypatch.setattr(huggingface_hub.HfApi,'model_info',lambda *args,**kwargs:SimpleNamespace(sha='a'*40))
    monkeypatch.setattr(huggingface_hub,'hf_hub_download',lambda *args,**kwargs:str(bad))
    with pytest.raises(ValueError,match='not chat LLMs'):
        resolve_source(tmp_path,[],'custom','https://huggingface.co/test/llm')
    assert not (tmp_path/'models').exists()
