from dataclasses import replace
from pathlib import Path
import time
import numpy as np
import pytest
from tinysearch.config import Config
from tinysearch.worker import ManagedEngine

def fake_worker(connection,config):
    connection.send({'ok':True,'metrics':{'fake':True}})
    try:
        while True:
            command,argument=connection.recv()
            if command=='close':break
            if isinstance(argument,str) and argument=='fail':
                connection.send({'ok':False,'error':'bad input'});continue
            if isinstance(argument,str) and argument=='slow': time.sleep(1.3)
            connection.send({'ok':True,'result':np.ones(128,dtype=np.float32),'metrics':{}})
    except EOFError: pass

def failed_load(connection,config): connection.send({'ok':False,'error':'unavailable weights'})

def fake_vision_worker(connection,config):
    connection.send({'ok':True,'metrics':{'vision_loaded':True}})
    try:
        while True:
            command,argument=connection.recv()
            if command=='close': break
            connection.send({'ok':True,'result':np.full(128,2,dtype=np.float32),'metrics':{'vision_loaded':True}})
    except EOFError: pass

def wait_until(condition,timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if condition(): return
        time.sleep(.05)
    pytest.fail('State did not change before deadline')

def config(tmp_path): return Config((tmp_path,),tmp_path/'model','test',tmp_path/'index.sqlite3',dimensions=128,idle_seconds=1)

def test_lazy_load_idle_exit_and_reload(tmp_path):
    manager=ManagedEngine(config(tmp_path),target=fake_worker)
    try:
        assert manager.metrics()['state']=='unloaded'
        assert manager.process is None
        manager.query('hello');pid=manager.metrics()['worker_pid']
        assert manager.metrics()['loads']==1
        # Read-only status polling must not keep the worker alive.
        wait_until(lambda: manager.metrics()['state']=='unloaded')
        assert manager.process is None
        assert manager.metrics()['unloads']==1
        manager.query('again')
        assert manager.metrics()['worker_pid']!=pid
        assert manager.metrics()['loads']==2
    finally: manager.close()

def test_pin_and_inflight_search_protect_worker(tmp_path):
    manager=ManagedEngine(config(tmp_path),target=fake_worker)
    try:
        with manager.pin():
            manager.query('hello');time.sleep(1.6)
            assert manager.metrics()['state']=='ready'
            with pytest.raises(RuntimeError,match='Indexing'): manager.unload()
        manager.query('slow')
        assert manager.metrics()['state']=='ready'
        wait_until(lambda: manager.metrics()['state']=='unloaded')
    finally: manager.close()

def test_activation_error_and_bad_request_recover(tmp_path):
    manager=ManagedEngine(config(tmp_path),target=failed_load)
    try:
        manager.activate();wait_until(lambda:manager.metrics()['state']=='error')
        assert manager.process is None
        manager.target=fake_worker
        manager.query('hello')
        with pytest.raises(ValueError,match='bad input'):manager.query('fail')
        assert manager.query('hello').shape==(128,)
        assert manager.metrics()['loads']==1
    finally:manager.close()

def test_failed_process_spawn_is_reported_and_cleaned(tmp_path,monkeypatch):
    import multiprocessing.process
    def fail(*args,**kwargs):raise OSError('Cannot start worker')
    monkeypatch.setattr(multiprocessing.process.BaseProcess,'start',fail)
    manager=ManagedEngine(config(tmp_path),target=fake_worker)
    try:
        with pytest.raises(OSError,match='Cannot start'):manager.query('hello')
        assert manager.metrics()['state']=='error'
        assert manager.process is None and manager.connection is None
        assert manager.monitor.is_alive()
    finally:manager.close()

def test_configuration_roundtrip_and_distinct_spaces(tmp_path):
    from tinysearch.settings import configured
    original=replace(config(tmp_path),source_path=tmp_path/'config.toml')
    original.save();assert Config.load(original.source_path)==original
    values=dict(model_variant='mlx-8bit',dimensions=256,enable_documents=True,enable_images=True,thumbnail_px=256,chunk_tokens=384,idle_seconds=60)
    selected=configured(original,values)
    assert selected.backend=='mlx'
    assert selected.database!=original.database
    assert configured(original,{**values,'dimensions':128}).database!=selected.database
    assert configured(original,{**values,'enable_images':False}).database!=selected.database
    assert configured(original,{**values,'chunk_tokens':512}).database!=selected.database
    assert configured(replace(original,roots=(tmp_path/'other',)),values).database!=selected.database


def test_audio_worker_is_lazy_and_search_does_not_reload_it(tmp_path,monkeypatch):
    import tinysearch.worker as worker
    monkeypatch.setattr(worker,'audio_worker_main',fake_worker)
    selected=replace(config(tmp_path),backend='mlx',enable_audio=True,idle_seconds=60)
    manager=ManagedEngine(selected,target=fake_worker)
    try:
        manager.query('text');assert manager.audio_manager is None
        manager.audio(np.ones(16000,dtype=np.float32))
        assert manager.audio_manager.process.is_alive()
        audio_pid=manager.metrics()['audio']['worker_pid']
        assert audio_pid!=manager.metrics()['worker_pid']
        manager.release_audio()
        assert manager.audio_manager.process is None
        manager.query('find a sound')
        assert manager.metrics()['audio']['state']=='unloaded'
        assert manager.metrics()['audio']['loads']==1
    finally: manager.close()


def test_search_and_vision_modes_preserve_index_identity_and_do_not_overlap(tmp_path):
    import os
    from tinysearch.engine import engine_identity
    selected=replace(config(tmp_path),backend='mlx',enable_video=True,idle_seconds=60)
    manager=ManagedEngine(selected,target=fake_worker,vision_target=fake_vision_worker)
    try:
        original_identity=manager.identity
        assert original_identity==engine_identity(selected)
        manager.activate();wait_until(lambda:manager.metrics()['state']=='ready')
        text_pid=manager.metrics()['worker_pid']
        assert not manager.metrics()['vision_loaded']
        with manager.pin():
            assert manager.image(tmp_path/'synthetic.png')[0]==2
            vision_pid=manager.metrics()['worker_pid']
            assert vision_pid!=text_pid and manager.metrics()['vision_loaded']
            with pytest.raises(ProcessLookupError): os.kill(text_pid,0)
            assert manager.document('document in indexing pass')[0]==2
            assert manager.video([])[0]==2
            assert manager.metrics()['worker_pid']==vision_pid
            manager.release_vision()
            assert manager.metrics()['state']=='unloaded'
            with pytest.raises(ProcessLookupError): os.kill(vision_pid,0)
        assert manager.query('find a photograph')[0]==1
        assert not manager.metrics()['vision_loaded']
        assert manager.identity==original_identity
        assert manager.config==selected
    finally: manager.close()


def test_text_query_downgrades_vision_outside_indexing_and_disabled_modes_stay_unloaded(tmp_path):
    manager=ManagedEngine(replace(config(tmp_path),idle_seconds=60),target=fake_worker,vision_target=fake_vision_worker)
    try:
        manager.image(tmp_path/'synthetic.png')
        assert manager.metrics()['vision_loaded']
        assert manager.query('a photograph')[0]==1
        assert not manager.metrics()['vision_loaded']
    finally: manager.close()
    disabled=ManagedEngine(replace(config(tmp_path),enable_images=False),target=fake_worker)
    try:
        with pytest.raises(ValueError,match='disabled'): disabled.image(tmp_path/'synthetic.png')
        with pytest.raises(ValueError,match='disabled'): disabled.video([])
        assert disabled.process is None
    finally: disabled.close()
