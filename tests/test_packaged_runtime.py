"""Fresh installs and the bundled codecs must work without external tools."""
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
from PIL import Image
import pytest
from reportlab.pdfgen import canvas
from fastapi.testclient import TestClient
from test_store import FakeEngine
from tinysearch.config import Config
from tinysearch.runtime import initial_config
from tinysearch.server import create_app
from tinysearch.settings import configured
from tinysearch import media
from tinysearch.preview import render_pdf


def test_first_launch_has_no_selected_files_and_preserves_existing_configuration(tmp_path):
    storage=tmp_path/'application support'
    config=initial_config(storage,port=8877)
    assert config.roots==() and config.dimensions==512 and config.backend=='mlx'
    assert config.ffmpeg=='pyav' and config.pdf_renderer=='pdfium'
    assert not config.database.exists()
    root=tmp_path/'chosen';root.mkdir()
    config=replace(config,roots=(root,));config.save()
    saved=config.source_path.read_bytes()
    repeated=initial_config(storage,8877)
    assert repeated.roots==(root,) and config.source_path.read_bytes()==saved
    with TestClient(create_app(repeated,FakeEngine())) as client:
        assert client.get('/v1/info').json()==dict(application='semantic-search',version=__import__('tinysearch').__version__,protocol=1)
        assert client.get('/v1/settings').json()['current']['roots']==[str(root)]


def test_selected_folders_validate_and_select_a_separate_index(tmp_path):
    config=initial_config(tmp_path/'support')
    first=tmp_path/'first';first.mkdir()
    second=tmp_path/'second';second.mkdir()
    values=dict(model_variant='mlx-8bit',dimensions=512,chunk_tokens=512,enable_documents=True,enable_images=True,thumbnail_px=256,idle_seconds=60)
    a=configured(config,{**values,'roots':[str(first),str(first)]})
    b=configured(a,{**values,'roots':[str(second)]})
    assert a.roots==(first,) and b.roots==(second,) and a.database!=b.database
    assert a.model_path==b.model_path and config.roots==()
    for folder in [str(tmp_path/'missing'), 'relative/folder']:
        with pytest.raises(ValueError): configured(config,{**values,'roots':[folder]})
    with TestClient(create_app(config,FakeEngine())) as client:
        assert client.post('/v1/settings',json=values).status_code==422
        assert 'folder' in client.post('/v1/settings',json=values).json()['detail'].lower()


def make_clip(path):
    import av
    from fractions import Fraction
    with av.open(str(path),'w') as out:
        stream=out.add_stream('mpeg4',rate=10)
        stream.width=32;stream.height=24;stream.pix_fmt='yuv420p'
        sound=out.add_stream('aac',rate=16000);sound.layout='mono'
        for index in range(51):
            frame=av.VideoFrame.from_image(Image.new('RGB',(32,24),'blue'))
            frame.pts=index;frame.time_base=Fraction(1,10)
            for packet in stream.encode(frame):out.mux(packet)
        for packet in stream.encode(None):out.mux(packet)
        samples=(np.sin(np.arange(84800)*2*np.pi*1000/16000)*.5).astype(np.float32)
        for offset in range(0,len(samples),1024):
            frame=av.AudioFrame.from_ndarray(samples[offset:offset+1024].reshape(1,-1),format='fltp',layout='mono')
            frame.sample_rate=16000;frame.pts=offset;frame.time_base=Fraction(1,16000)
            for packet in sound.encode(frame):out.mux(packet)
        for packet in sound.encode(None):out.mux(packet)


def test_bundled_media_and_pdf_without_homebrew(tmp_path,monkeypatch):
    monkeypatch.setenv('PATH','/usr/bin:/bin')
    config=initial_config(tmp_path/'support')
    path=tmp_path/'synthetic.mp4';make_clip(path)
    info=media.probe(path,config)
    assert info.has_video and info.has_audio and info.audio_duration>info.video_duration
    assert info.video_duration==pytest.approx(5.1,.02)
    frames=media.video_frames(path,5,5.1,config)
    assert len(frames)==3 and frames[0].size==(32,24)
    assert frames[0].getpixel((0,0))[2]>200
    samples=media.audio_samples(path,1,3,config)
    assert 31000<len(samples)<=32000 and np.isfinite(samples).all()
    assert .2<float(np.std(samples))<.5
    assert media.decoder_available(config)
    wave=tmp_path/'segment.wav'
    media.render_segment(path,wave,1,3,config,False)
    assert media.probe(wave,config).duration==pytest.approx(2,abs=.02)
    pdf=tmp_path/'synthetic.pdf';draw=canvas.Canvas(str(pdf));draw.drawString(40,200,'Synthetic PDF preview');draw.showPage();draw.save()
    image,pages=render_pdf(pdf,'pdfium')
    assert pages==1 and image.startswith(b'\xff\xd8')
    with Image.open(BytesIO(image)) as bitmap: assert max(bitmap.size)==1400


@pytest.mark.skipif(sys.platform!='darwin',reason='VideoToolbox is supplied by macOS')
def test_video_segment_uses_system_videotoolbox_without_homebrew(tmp_path,monkeypatch):
    monkeypatch.setenv('PATH','/usr/bin:/bin')
    config=initial_config(tmp_path/'support')
    path=tmp_path/'synthetic.mp4';make_clip(path)
    movie=tmp_path/'segment.mp4'
    media.render_segment(path,movie,1,3,config,True)
    clip_info=media.probe(movie,config)
    assert clip_info.has_video and clip_info.has_audio and clip_info.duration==pytest.approx(2,abs=.15)
