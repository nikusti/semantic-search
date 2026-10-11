from dataclasses import replace
from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from test_store import FakeEngine
from tinysearch import media
from tinysearch.config import Config
from tinysearch.store import Store
from tinysearch.server import create_app


def test_windows_cover_tail_without_redundant_last_window():
    assert list(media.segments(41,20,2)) == [(0,20),(18,38),(36,41)]
    assert list(media.segments(38,20,2)) == [(0,20),(18,38)]
    assert list(media.segments(21,10)) == [(0,10),(10,20),(20,21)]


def test_disabled_modality_settings_do_not_invalidate_enabled_index(tmp_path):
    from tinysearch.engine import engine_identity
    config=media_config(tmp_path)
    audio_only=replace(config,enable_video=False)
    assert engine_identity(audio_only)==engine_identity(replace(audio_only,video_segment_seconds=30,video_frames=8))
    assert engine_identity(audio_only)!=engine_identity(replace(audio_only,audio_segment_seconds=30))
    video_only=replace(config,enable_audio=False)
    assert engine_identity(video_only)==engine_identity(replace(video_only,audio_segment_seconds=30,audio_overlap_seconds=4))
    assert engine_identity(video_only)!=engine_identity(replace(video_only,video_segment_seconds=30))


class MediaEngine(FakeEngine):
    def video(self,frames): return self.document('scene')
    def audio(self,samples): return self.document('sound')
    def release_audio(self): self.released=True


def media_config(tmp_path, **values):
    root=tmp_path/'files';root.mkdir()
    return Config((root,),tmp_path/'model','test',tmp_path/'index.sqlite3',backend='mlx',dimensions=128,
                  enable_video=True,enable_audio=True,**values)


def patch_decoders(monkeypatch,duration=21):
    monkeypatch.setattr(media,'probe',lambda path,config:media.MediaInfo(duration,path.suffix=='.mp4',True))
    monkeypatch.setattr(media,'video_frames',lambda *args:[Image.new('RGB',(40,30),'blue')]*3)
    monkeypatch.setattr(media,'audio_samples',lambda *args:np.ones(16000,dtype=np.float32))


def test_media_index_reuses_duplicates_and_preserves_timestamps(tmp_path,monkeypatch):
    config=media_config(tmp_path);root=config.roots[0]
    first=root/'clip.mp4';first.write_bytes(b'video');audio=root/'sound.mp3';audio.write_bytes(b'audio')
    patch_decoders(monkeypatch)
    engine=MediaEngine();store=Store(config,engine)
    report=store.index()
    assert report['indexed']==2 and report['errors']==[] and engine.released
    # Three video windows + two soundtrack windows, two standalone audio windows.
    assert store.status()['chunks']==7
    result=store.search('sound','video')['results'][0]
    assert result['matched_modality']=='audio' and (result['start_seconds'],result['end_seconds'])==(0,20)
    assert result['duration_seconds']==21 and result['thumbnail'].startswith('data:image/jpeg')
    call_count=engine.calls
    duplicate=root/'duplicate.mp4';duplicate.write_bytes(first.read_bytes())
    report=store.index();assert report['reused']==1 and engine.calls==call_count
    assert store.db.execute('select count(*) from media_files').fetchone()[0]==3
    rows=store.storage_by_kind()
    assert next(r for r in rows if r['kind']=='video')['vector_bytes']==10*128*4
    first.unlink();store.index()
    assert store.db.execute('select count(*) from media_files').fetchone()[0]==2
    store.close()


def test_disabled_media_never_decodes(tmp_path,monkeypatch):
    config=replace(media_config(tmp_path),enable_audio=False,enable_video=False)
    (config.roots[0]/'a.mp4').write_bytes(b'video')
    (config.roots[0]/'a.mp3').write_bytes(b'audio')
    monkeypatch.setattr(media,'probe',lambda *args:pytest.fail('Disabled media was decoded'))
    store=Store(config,MediaEngine());assert store.index()['indexed']==0;store.close()

def test_audio_indexes_video_soundtracks_without_visual_encoder(tmp_path,monkeypatch):
    config=replace(media_config(tmp_path),enable_video=False)
    (config.roots[0]/'clip.mp4').write_bytes(b'video soundtrack')
    patch_decoders(monkeypatch,duration=10)
    monkeypatch.setattr(media,'video_frames',lambda *a:pytest.fail('Visual encoder must not run'))
    store=Store(config,MediaEngine())
    assert store.index()['indexed']==1
    assert set(row[0] for row in store.db.execute('SELECT modality FROM chunks'))=={'audio'}
    store.close()


def test_real_video_tail_with_longer_soundtrack(tmp_path):
    import shutil
    import subprocess
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Local ffmpeg and ffprobe are required')
    config=media_config(tmp_path,video_segment_seconds=5)
    path=config.roots[0]/'clip.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
                    'color=c=blue:s=32x24:r=10:d=5.1','-f','lavfi','-i',
                    'sine=frequency=1000:duration=5.3','-c:v','libx264',
                    '-pix_fmt','yuv420p','-c:a','aac','-threads','1',str(path)],
                   check=True,capture_output=True,timeout=20)
    info=media.probe(path,config)
    assert info.audio_duration > info.video_duration
    store=Store(config,MediaEngine())
    try:
        result=store.index()
        assert result['indexed']==1 and result['errors']==[]
        ends=dict(store.db.execute('SELECT modality,max(end_seconds) FROM chunks GROUP BY modality'))
        assert ends['video']==pytest.approx(info.video_duration)
        assert ends['audio']==pytest.approx(info.audio_duration)
    finally:
        store.close()


def test_duration_limit_and_segment_guards(tmp_path,monkeypatch):
    config=media_config(tmp_path,max_media_seconds=20);root=config.roots[0]
    path=root/'clip.mp4';path.write_bytes(b'video')
    patch_decoders(monkeypatch)
    store=Store(config,MediaEngine());report=store.index()
    assert report['indexed']==0 and 'skipped, not truncated' in report['errors'][0]
    store.close()
    config=replace(config,max_media_seconds=60)
    with TestClient(create_app(config,MediaEngine())) as client:
        assert client.post('/v1/index').json()['indexed']==1
        def render(path,output,start,end,config,video): output.write_bytes(b'local clip')
        monkeypatch.setattr(media,'render_segment',render)
        assert client.post('/v1/segment',json={'path':str(path),'start_seconds':0,'end_seconds':10}).status_code==200
        assert client.post('/v1/segment',json={'path':str(path),'start_seconds':1,'end_seconds':10}).status_code==404
        assert client.post('/v1/segment',json={'path':str(root.parent/'private.mp4'),'start_seconds':0,'end_seconds':10}).status_code==404
        assert client.post('/v1/preview',json={'path':str(path),'start_seconds':100}).status_code==404
        path.unlink();path.symlink_to(root.parent/'private.mp4')
        assert client.post('/v1/segment',json={'path':str(path),'start_seconds':0,'end_seconds':10}).status_code==404
