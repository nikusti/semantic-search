import json
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient
import pytest

from tinysearch.automatic import AutomaticIndexing
from tinysearch.config import Config
from tinysearch.server import create_app
from tinysearch import launcher
from test_store import FakeEngine


OWNER = {'pid': 12345, 'started': 'Mon Oct 12 12:00:00 2026',
         'executable': '/Applications/Tinycast.app/Contents/MacOS/Tinycast',
         'host': 'Tinycast', 'extension_path': '/private/tmp/synthetic-extension',
         'extension_marker': '1:2'}


def wait_for(check):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.005)
    raise AssertionError('Operation did not reach the expected state')


@pytest.fixture
def service(tmp_path):
    root = tmp_path/'files'
    root.mkdir()
    config = Config((root,),tmp_path/'model','test',tmp_path/'data/index.sqlite3',
                    dimensions=128,storage_path=tmp_path,source_path=tmp_path/'config.toml')
    config.save()
    app = create_app(config,FakeEngine())
    with TestClient(app) as client:
        automatic = app.state.settings.automatic
        automatic.probe = lambda owner: bool(owner)
        automatic.power = lambda: True
        automatic.poll_seconds = .01
        yield client,app,root


def enable(client, **settings):
    reply = client.post('/v1/automatic',json={'enabled':True,'launcher':OWNER,**settings})
    assert reply.status_code == 200,reply.text
    return reply.json()


def settle(app):
    wait_for(lambda: app.state.settings.job['state'] in {'complete','paused','cancelled','error'})
    wait_for(lambda: not app.state.settings.thread.is_alive())
    assert app.state.settings.job['state'] != 'error',app.state.settings.job
    return app.state.settings.job


def blocked_files(app,root):
    for name in ['a.txt','b.txt','c.txt']:
        (root/name).write_text(name)
    entered,release = threading.Event(),threading.Event()
    original = app.state.engine.document
    def document(text):
        if text == 'b.txt':
            entered.set()
            assert release.wait(5)
        return original(text)
    app.state.engine.document = document
    return entered,release,original


def test_disabled_by_default_does_not_start_an_engine_or_timer(service):
    client,app,root = service
    automatic=app.state.settings.automatic
    automatic.power=lambda: pytest.fail('Disabled scheduler read power state')
    automatic.probe=lambda owner: pytest.fail('Disabled scheduler read launcher state')
    assert automatic.tick(None)['state']=='disabled'
    assert app.state.settings.thread is None
    assert not automatic.path.exists()
    assert not app.state.store.index_lock.locked()


def test_schedule_is_shared_between_hosts_and_survives_restart(service):
    client,app,root=service
    (root/'note.txt').write_text('A synthetic note')
    automatic=app.state.settings.automatic
    now=[1_000_000.]
    automatic.clock=lambda:now[0]
    enable(client,interval_seconds=3600)
    assert client.post('/v1/automatic/tick',json={'launcher':OWNER}).json()['state']=='indexing'
    settle(app)
    other={**OWNER,'host':'Raycast','pid':23456}
    assert automatic.tick(other)['state']=='scheduled'
    assert app.state.store.status()['files']==1
    calls=app.state.engine.calls
    restored=AutomaticIndexing(app.state.settings,clock=automatic.clock,probe=lambda o:True,power=lambda:True)
    assert restored.status()['enabled'] and restored.status()['interval_seconds']==3600
    assert restored.tick(other)['state']=='scheduled'
    now[0]+=3600
    assert automatic.tick(other)['state']=='indexing'
    settle(app)
    assert app.state.engine.calls==calls
    assert app.state.store.status()['last_index_summary']['unchanged']==1


def test_power_condition_waits_without_advancing_schedule(service):
    client,app,root=service
    automatic=app.state.settings.automatic
    enable(client)
    automatic.power=lambda:False
    assert automatic.tick(OWNER)['state']=='waiting_for_power'
    assert automatic.status()['last_started_at'] is None
    assert app.state.settings.thread is None
    automatic.power=lambda:None
    assert automatic.tick(OWNER)['last_reason']=='power_unavailable'


def test_pause_retains_completed_files_and_resume_encodes_only_remaining_files(service):
    client,app,root=service
    entered,release,original=blocked_files(app,root)
    try:
        assert client.post('/v1/index/start',json={}).status_code==200
        assert entered.wait(5)
        assert app.state.store.status()['files']==1
        assert client.post('/v1/index/pause').json()['pausing']
    finally:
        release.set()
    assert settle(app)['state']=='paused'
    assert app.state.store.status()['files']==1
    assert app.state.store.status()['last_index_summary']['paused']
    assert not app.state.store.db.execute('SELECT 1 FROM index_failures').fetchone()
    calls=app.state.engine.calls
    app.state.engine.document=original
    assert client.post('/v1/index/resume',json={}).status_code==200
    settle(app)
    assert app.state.store.status()['files']==3
    assert app.state.engine.calls-calls==2
    assert app.state.store.status()['last_index_summary']['unchanged']==1
    assert not app.state.settings.automatic.status()['paused']


@pytest.mark.parametrize('cause',['launcher_closed','on_battery'])
def test_auto_interrupts_and_next_valid_tick_continues_committed_entries(service,cause):
    client,app,root=service
    automatic=app.state.settings.automatic
    alive,powered=[True],[True]
    automatic.probe=lambda owner:alive[0]
    automatic.power=lambda:powered[0]
    entered,release,original=blocked_files(app,root)
    enable(client)
    try:
        assert automatic.tick(OWNER)['state']=='indexing'
        assert entered.wait(5)
        if cause=='launcher_closed': alive[0]=False
        else: powered[0]=False
        wait_for(lambda:app.state.settings.job.get('interrupt_reason')==cause)
    finally:
        release.set()
    job=settle(app)
    assert job['state']==('cancelled' if cause=='launcher_closed' else 'paused')
    assert app.state.store.status()['files']==1
    assert automatic.status()['pending_resume']
    alive[0]=powered[0]=True
    app.state.engine.document=original
    assert automatic.tick(OWNER)['state']=='indexing'
    settle(app)
    assert app.state.store.status()['files']==3
    assert app.state.store.status()['last_index_summary']['unchanged']==1
    assert not automatic.status()['pending_resume']


def test_scheduled_ticks_cannot_overlap_even_after_a_long_sleep(service):
    client,app,root=service
    automatic=app.state.settings.automatic
    now=[1_000_000.]
    automatic.clock=lambda:now[0]
    entered,release,_=blocked_files(app,root)
    enable(client,interval_seconds=3600)
    try:
        assert automatic.tick(OWNER)['state']=='indexing'
        assert entered.wait(5)
        original_thread=app.state.settings.thread
        now[0]+=86400
        assert automatic.tick({**OWNER,'host':'Raycast'})['state']=='busy'
        assert app.state.settings.thread is original_thread
    finally:
        release.set()
    settle(app)
    assert automatic.tick(OWNER)['state']=='scheduled'


def test_disable_stops_automatic_job_but_preserves_manual_job(service):
    client,app,root=service
    entered,release,original=blocked_files(app,root)
    enable(client)
    try:
        app.state.settings.automatic.tick(OWNER)
        assert entered.wait(5)
        assert client.post('/v1/automatic',json={'enabled':False}).status_code==200
    finally: release.set()
    assert settle(app)['state']=='cancelled'
    assert not app.state.settings.automatic.status()['pending_resume']
    app.state.engine.document=original
    enable(client)
    entered,release,_=blocked_files(app,root)
    (root/'b.txt').write_text('b.txt')
    try:
        client.post('/v1/index/start',json={'launcher':OWNER})
        assert entered.wait(5)
        client.post('/v1/automatic',json={'enabled':False})
        assert not app.state.store.cancel_requested.is_set()
    finally: release.set()
    assert settle(app)['state']=='complete'


def test_crash_recovery_marks_continuation_without_starting_work(service):
    client,app,root=service
    automatic=app.state.settings.automatic
    enable(client)
    saved=automatic.status()
    saved.update(in_progress=True,last_started_at=time.time())
    automatic.path.write_text(json.dumps(saved))
    recovered=AutomaticIndexing(app.state.settings)
    assert recovered.status()['pending_resume']
    assert not recovered.status()['in_progress']
    assert app.state.settings.thread is None


def test_invalid_preferences_fail_closed_and_invalid_owner_cannot_enable(service):
    client,app,root=service
    automatic=app.state.settings.automatic
    automatic.path.write_text('[]')
    recovered=AutomaticIndexing(app.state.settings)
    assert not recovered.status()['enabled']
    automatic.probe=lambda owner:False
    assert client.post('/v1/automatic',json={'enabled':True,'launcher':OWNER}).status_code==422
    assert not automatic.status()['enabled']


def test_owner_checks_process_birth_and_removed_extension(monkeypatch,tmp_path):
    folder=tmp_path/'extension'
    folder.mkdir()
    (folder/'package.json').write_text(json.dumps({'name':'semantic-search'}))
    owner={**OWNER,'extension_path':str(folder),'extension_marker':launcher.extension_marker(folder)}
    process={'pid':owner['pid'],'parent':1,'started':owner['started'],'executable':owner['executable']}
    monkeypatch.setattr(launcher,'process_snapshot',lambda pid:process)
    monkeypatch.setattr(launcher,'launcher_name',lambda executable:'Tinycast')
    assert launcher.owner_alive(owner)
    process['started']='a reused PID'
    assert not launcher.owner_alive(owner)
    process['started']=owner['started']
    folder.rename(tmp_path/'uninstalled-extension')
    folder.mkdir()
    (folder/'package.json').write_text(json.dumps({'name':'semantic-search'}))
    assert not launcher.owner_alive(owner)


def test_resume_during_pausing_is_rejected_without_losing_the_pause(service):
    client,app,root=service
    entered,release,original=blocked_files(app,root)
    enable(client)
    try:
        app.state.settings.automatic.tick(OWNER)
        assert entered.wait(5)
        client.post('/v1/index/pause')
        response=client.post('/v1/index/resume',json={'launcher':OWNER})
        assert response.status_code==409
        assert app.state.settings.automatic.status()['paused']
    finally:
        release.set()
    assert settle(app)['state']=='paused'
    app.state.engine.document=original
    assert client.post('/v1/index/resume',json={'launcher':OWNER}).json()['state']=='indexing'
    assert settle(app)['state']=='complete'
    assert app.state.store.status()['files']==3


def test_manual_pause_can_resume_after_service_restart(service):
    client,app,root=service
    entered,release,_=blocked_files(app,root)
    try:
        client.post('/v1/index/start',json={})
        assert entered.wait(5)
        client.post('/v1/index/pause')
    finally:
        release.set()
    settle(app)
    assert app.state.settings.automatic.status()['continuation_origin']=='manual'
    config=app.state.store.config
    with TestClient(create_app(config,FakeEngine())) as restored:
        restored.app.state.settings.automatic.probe=lambda owner:bool(owner)
        assert restored.app.state.settings.job['state']=='idle'
        response=restored.post('/v1/index/resume',json={'launcher':OWNER})
        assert response.status_code==200,response.text
        assert settle(restored.app)['state']=='complete'
        assert restored.app.state.store.status()['files']==3
        assert restored.app.state.store.status()['last_index_summary']['unchanged']==1


def test_cancel_a_power_paused_job_discards_immediate_continuation(service):
    client,app,root=service
    automatic=app.state.settings.automatic
    now=[1_000_000.]
    automatic.clock=lambda:now[0]
    entered,release,_=blocked_files(app,root)
    enable(client)
    try:
        automatic.tick(OWNER)
        assert entered.wait(5)
        automatic.power=lambda:False
        wait_for(lambda:app.state.settings.job.get('interrupt_reason')=='on_battery')
    finally:
        release.set()
    settle(app)
    assert automatic.status()['pending_resume']
    now[0]+=86400
    assert client.post('/v1/index/cancel').json()['state']=='cancelled'
    assert not automatic.status()['pending_resume']
    automatic.power=lambda:True
    assert automatic.tick(OWNER)['state']=='scheduled'
    assert app.state.store.status()['files']==1


def test_malformed_extension_and_app_metadata_fail_closed(monkeypatch,tmp_path):
    folder=tmp_path/'extension'
    folder.mkdir()
    (folder/'package.json').write_text('{"name":"semantic-search"}')
    owner={**OWNER,'extension_path':str(folder),'extension_marker':launcher.extension_marker(folder)}
    process={'pid':owner['pid'],'started':owner['started'],'executable':owner['executable']}
    monkeypatch.setattr(launcher,'process_snapshot',lambda pid:process)
    monkeypatch.setattr(launcher,'launcher_name',lambda executable:'Tinycast')
    (folder/'package.json').write_text('[]')
    assert not launcher.owner_alive(owner)
    monkeypatch.undo()
    import plistlib
    native=tmp_path/'Tinycast.app/Contents/MacOS/Tinycast'
    native.parent.mkdir(parents=True)
    (native.parent.parent/'Info.plist').write_bytes(plistlib.dumps([]))
    assert launcher.launcher_name(str(native)) is None


def test_owner_loss_before_acquiring_index_lock_remains_monitored(service):
    from contextlib import contextmanager
    client,app,root=service
    (root/'a.txt').write_text('Synthetic text')
    before_index,enter_index,in_scan,release_scan,observed_loss=[threading.Event() for _ in range(5)]
    alive=[True]
    automatic=app.state.settings.automatic
    def probe(owner):
        if not alive[0]:
            observed_loss.set()
        return alive[0]
    automatic.probe=probe
    @contextmanager
    def pin():
        before_index.set()
        assert enter_index.wait(5)
        yield
    original_scan=app.state.store._scan
    def scan(root):
        in_scan.set()
        assert release_scan.wait(5)
        return original_scan(root)
    app.state.engine.pin=pin
    app.state.store._scan=scan
    enable(client)
    try:
        automatic.tick(OWNER)
        assert before_index.wait(5)
        alive[0]=False
        assert observed_loss.wait(5)
        assert app.state.settings.job.get('interrupt_reason') is None
        enter_index.set()
        assert in_scan.wait(5)
        wait_for(lambda:app.state.settings.job.get('interrupt_reason')=='launcher_closed')
    finally:
        enter_index.set()
        release_scan.set()
    assert settle(app)['state']=='cancelled'
    assert app.state.store.status()['files']==0


def test_finishing_worker_prevents_a_new_job_until_completion_is_saved(service):
    client,app,root=service
    (root/'a.txt').write_text('Synthetic text')
    automatic=app.state.settings.automatic
    entered,release=threading.Event(),threading.Event()
    completed=automatic.completed
    def delayed(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        completed(*args,**kwargs)
    automatic.completed=delayed
    try:
        client.post('/v1/index/start',json={})
        assert entered.wait(5)
        assert not app.state.store.index_lock.locked()
        response=client.post('/v1/index/start',json={})
        assert response.status_code==409
        assert app.state.settings.thread.is_alive()
    finally:
        release.set()
    settle(app)


@pytest.mark.parametrize('asynchronous',[False,True])
def test_successful_manual_update_supersedes_pending_automatic_continuation(service,asynchronous):
    client,app,root=service
    automatic=app.state.settings.automatic
    entered,release,original=blocked_files(app,root)
    enable(client)
    try:
        automatic.tick(OWNER)
        assert entered.wait(5)
        automatic.power=lambda:False
        wait_for(lambda:app.state.settings.job.get('interrupt_reason')=='on_battery')
    finally: release.set()
    settle(app)
    assert automatic.status()['pending_resume']
    automatic.power=lambda:True
    app.state.engine.document=original
    reply=client.post('/v1/index/start' if asynchronous else '/v1/index',json={})
    assert reply.status_code==200,reply.text
    if asynchronous: settle(app)
    assert app.state.store.status()['files']==3
    assert not automatic.status()['pending_resume']
    assert automatic.tick(OWNER)['state']=='scheduled'


def test_paused_manual_continuation_can_be_cancelled_after_restart(service):
    client,app,root=service
    entered,release,_=blocked_files(app,root)
    try:
        client.post('/v1/index/start',json={})
        assert entered.wait(5)
        client.post('/v1/index/pause')
    finally: release.set()
    settle(app)
    with TestClient(create_app(app.state.store.config,FakeEngine())) as restored:
        assert restored.app.state.settings.automatic.status()['continuation_origin']=='manual'
        reply=restored.post('/v1/index/cancel')
        assert reply.json()['state']=='cancelled'
        state=restored.app.state.settings.automatic.status()
        assert not state['paused'] and state['continuation_origin'] is None
        assert restored.app.state.store.status()['files']==1


def test_cancelled_continuation_cannot_race_another_hosts_tick(service):
    from concurrent.futures import ThreadPoolExecutor
    client,app,root=service
    automatic=app.state.settings.automatic
    entered,release,original=blocked_files(app,root)
    enable(client)
    try:
        automatic.tick(OWNER)
        assert entered.wait(5)
        automatic.power=lambda:False
        wait_for(lambda:app.state.settings.job.get('interrupt_reason')=='on_battery')
    finally: release.set()
    settle(app)
    app.state.engine.document=original
    automatic.power=lambda:True
    completing,finish=threading.Event(),threading.Event()
    completed=automatic.completed
    def held_completion(*args,**kwargs):
        completing.set()
        assert finish.wait(5)
        return completed(*args,**kwargs)
    automatic.completed=held_completion
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            cancel=pool.submit(client.post,'/v1/index/cancel')
            assert completing.wait(5)
            tick=pool.submit(client.post,'/v1/automatic/tick',json={'launcher':OWNER})
            time.sleep(.05)
            assert not tick.done()
        finally: finish.set()
        assert cancel.result(5).json()['state']=='cancelled'
        assert tick.result(5).json()['state']=='scheduled'
    assert app.state.store.status()['files']==1
    assert not automatic.status()['in_progress']


def test_resuming_idle_scheduler_does_not_restart_a_completed_manual_scan_on_battery(service):
    client,app,root=service
    (root/'a.txt').write_text('Synthetic text')
    client.post('/v1/index/start',json={})
    settle(app)
    automatic=app.state.settings.automatic
    enable(client)
    client.post('/v1/index/pause')
    assert automatic.status()['continuation_origin'] is None
    automatic.power=lambda:False
    calls=app.state.engine.calls
    reply=client.post('/v1/index/resume',json={'launcher':OWNER})
    assert reply.status_code==200,reply.text
    assert not reply.json()['paused']
    assert app.state.settings.job['state']=='complete'
    assert not app.state.settings.thread.is_alive()
    assert app.state.engine.calls==calls
    assert automatic.tick(OWNER)['state']=='waiting_for_power'


@pytest.mark.parametrize('interval',[1800,3600])
def test_short_intervals_and_overdue_restart_do_not_reset_the_wait(service,interval):
    client,app,root=service
    automatic=app.state.settings.automatic
    assert automatic.status()['interval_seconds']==3600
    now=[1_000_000.]
    automatic.clock=lambda:now[0]
    (root/'note.txt').write_text('A synthetic note')
    enable(client,interval_seconds=interval)
    assert automatic.tick(OWNER)['state']=='indexing'
    settle(app)
    now[0]+=interval*1.5
    recovered=AutomaticIndexing(app.state.settings,clock=automatic.clock,probe=lambda owner:True,power=lambda:True)
    assert recovered.status()['interval_seconds']==interval
    assert recovered.tick(OWNER)['state']=='indexing'
    settle(app)


def test_removed_retained_source_is_not_updated_by_scheduled_passes(service):
    client,app,root=service
    sibling=root.parent/'other-source'
    sibling.mkdir()
    saved=sibling/'saved.txt';saved.write_text('Original synthetic content')
    (root/'active.txt').write_text('Active source content')
    client.post('/v1/folders/add',json={'paths':[str(sibling)]})
    client.post('/v1/index/start',json={});settle(app)
    old=app.state.store.db.execute('SELECT vector FROM chunks WHERE path=?',(str(saved),)).fetchone()[0]
    reply=client.post('/v1/folders/remove',json={'path':str(sibling),'delete_saved_index':False})
    assert reply.status_code==200 and reply.json()['deleted_entries']==0
    saved.write_text('Changed retained content that must not be processed')
    (sibling/'new.txt').write_text('Must not be indexed')
    automatic=app.state.settings.automatic
    enable(client,interval_seconds=1800)
    assert automatic.tick(OWNER,force=True)['state']=='indexing'
    settle(app)
    assert app.state.store.status()['files']==1
    assert app.state.store.status()['retained_files']==1
    assert app.state.store.db.execute('SELECT vector FROM chunks WHERE path=?',(str(saved),)).fetchone()[0]==old
    assert not app.state.store.db.execute('SELECT 1 FROM files WHERE path=?',(str(sibling/'new.txt'),)).fetchone()
    assert all(r['path'].startswith(str(root)+'/') for r in client.post('/v1/search',json={'query':'content'}).json()['results'])


def test_scheduled_pass_updates_every_active_folder_and_enabled_photos(service,monkeypatch):
    from dataclasses import replace
    from test_photos import library as photos_fixture
    client,app,root=service
    library=photos_fixture.__wrapped__(root.parent,monkeypatch)
    app.state.engine.image=lambda path:app.state.engine.document(path.read_bytes().hex())
    config=replace(app.state.store.config,enable_photos_library=True)
    config.save();app.state.store.config=config
    sibling=root.parent/'other-source';sibling.mkdir()
    (root/'a.txt').write_text('Initial source one')
    (sibling/'b.txt').write_text('Initial source two')
    client.post('/v1/folders/add',json={'paths':[str(sibling)]})
    automatic=app.state.settings.automatic
    enable(client,interval_seconds=1800)
    assert automatic.tick(OWNER,force=True)['state']=='indexing';settle(app)
    assert app.state.store.status()['files']==5
    (root/'a.txt').write_text('Updated source one')
    (sibling/'new.txt').write_text('New source two')
    library.rows.append({**library.rows[0],'id':'new-photo/L0/001','name':'Another.jpg'})
    assert automatic.tick(OWNER,force=True)['state']=='indexing';settle(app)
    summary=app.state.store.status()['last_index_summary']
    assert summary['added']==2 and summary['updated']==1
    assert app.state.store.status()['files']==7
    assert app.state.store.status()['photos_library']['indexed']==4
