"""One local data directory shared by Tinycast and Raycast."""
from dataclasses import replace
from pathlib import Path
import shutil

APP_SUPPORT = Path.home() / 'Library' / 'Application Support' / 'Semantic Search'


def migrate(config, legacy_root, destination=None):
    destination = Path(destination or config.storage_path or APP_SUPPORT).expanduser().resolve()
    legacy_root = Path(legacy_root).resolve()
    values = {'storage_path': destination}
    moves = []
    for field, folder in (('model_path', 'models'), ('database', 'data')):
        path = getattr(config, field)
        old = legacy_root / folder
        if path.is_relative_to(old) and old != destination / folder:
            values[field] = destination / folder / path.relative_to(old)
            moves.append((old, destination / folder))
    # Preflight every directory before moving anything. Never replace an existing
    # shared installation or copy live SQLite journal files.
    for old, new in moves:
        if old.is_symlink() or new.is_symlink():
            raise RuntimeError('Storage migration requires ordinary directories')
        if old.exists() and new.exists():
            raise RuntimeError(f'Both legacy and shared storage exist: {new}')
    destination.mkdir(parents=True, exist_ok=True)
    for old, new in moves:
        if old.exists():
            shutil.move(str(old), str(new))
    updated = replace(config, **values)
    if updated != config and updated.source_path:
        updated.save()
    return updated
