import hashlib
from pathlib import Path
import pytest
from tinysearch.config import Config
from tinysearch.storage import migrate


def test_migrate_shared_models_indexes_preserves_bytes_and_is_idempotent(tmp_path):
    old=tmp_path/'project';shared=tmp_path/'Application Support/Semantic Search'
    model=old/'models/recommended';model.mkdir(parents=True)
    (model/'model.safetensors').write_bytes(b'original weights')
    data=old/'data';data.mkdir();(data/'index.sqlite3').write_bytes(b'original index')
    (data/'historical.sqlite3').write_bytes(b'old test index')
    cfg=Config((tmp_path/'Downloads',),model,'pinned',data/'index.sqlite3',source_path=old/'config.toml',excluded_paths=(tmp_path/'Downloads/excluded-folder',))
    cfg.save()
    before={str(p.relative_to(old)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('models','data') for p in (old/folder).rglob('*') if p.is_file()}
    moved=migrate(cfg,old,shared)
    after={str(p.relative_to(shared)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('models','data') for p in (shared/folder).rglob('*') if p.is_file()}
    assert before==after and not (old/'models').exists() and not (old/'data').exists()
    assert moved.model_path==shared/'models/recommended' and moved.database==shared/'data/index.sqlite3'
    assert moved.storage_path==shared and moved.excluded_paths==cfg.excluded_paths
    assert Config.load(cfg.source_path)==moved and migrate(moved,old,shared)==moved


def test_migration_conflict_does_not_move_anything(tmp_path):
    old=tmp_path/'project';shared=tmp_path/'shared'
    (old/'models/recommended').mkdir(parents=True);(old/'data').mkdir()
    (old/'models/recommended/weights').write_text('keep weights')
    (old/'data/index.sqlite3').write_text('keep index')
    (shared/'data').mkdir(parents=True);(shared/'data/index.sqlite3').write_text('other index')
    cfg=Config((tmp_path,),old/'models/recommended','pinned',old/'data/index.sqlite3',source_path=old/'config.toml')
    cfg.save();before=cfg.source_path.read_text()
    with pytest.raises(RuntimeError,match='Both legacy and shared storage'):
        migrate(cfg,old,shared)
    assert (old/'models/recommended/weights').read_text()=='keep weights'
    assert (shared/'data/index.sqlite3').read_text()=='other index'
    assert cfg.source_path.read_text()==before
