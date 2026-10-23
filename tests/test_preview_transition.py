"""Refinement keeps one image format and geometry, without growing the cache."""
import base64
from io import BytesIO
import pytest
from PIL import Image, ImageDraw, ImageChops
from authenticated_client import TestClient
from tinysearch.config import Config
from tinysearch.server import create_app
from test_store import FakeEngine


@pytest.mark.parametrize('size', [(900,600), (400,900)])
def test_jpeg_transition_preserves_preview_and_cache(tmp_path, size):
    root=tmp_path/'files';root.mkdir();path=root/'photo.png'
    image=Image.new('RGB',size,'navy');draw=ImageDraw.Draw(image)
    for x in range(0,900,3):draw.line((x,0,900-x,599),fill=(x%256,100,200))
    image.save(path)
    config=Config((root,),tmp_path/'model','test',tmp_path/'index.sqlite3',dimensions=128)
    engine=FakeEngine();engine.image=lambda path:engine.document(str(path))
    with TestClient(create_app(config,engine)) as client:
        client.post('/v1/index')
        plain=client.post('/v1/preview',json={'path':str(path)}).json()
        animated=client.post('/v1/preview',json={'path':str(path),'animate':True}).json()
        assert animated['preview']==plain['preview'] and 'transition_frames' not in plain
        frames=animated['transition_frames']
        assert len(frames)==6 and frames[-1]==plain['preview']
        assert animated['transition_ms']==240
        decoded=[]
        for frame in frames:
            assert frame.startswith('data:image/jpeg;base64,')
            decoded.append(Image.open(BytesIO(base64.b64decode(frame.split(',',1)[1]))))
            assert decoded[-1].format=='JPEG' and decoded[-1].size==size
        assert ImageChops.difference(decoded[0],decoded[-1]).getbbox()
        cache=client.app.state.store.db.execute('SELECT jpeg FROM pdf_previews').fetchall()
        assert len(cache)==1 and bytes(cache[0][0]).startswith(b'\xff\xd8')
        assert client.post('/v1/preview',json={'path':str(root/'outside.png'),'animate':True}).status_code==404
