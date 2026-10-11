"""The portable setup helper writes only the explicitly selected local config."""
from pathlib import Path
import subprocess
import sys

from tinysearch.config import Config


def test_configure_creates_portable_settings_and_refuses_overwrite(tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    destination = tmp_path / "settings.toml"
    script = Path(__file__).resolve().parents[1] / "scripts/configure.py"
    command = [sys.executable, str(script), "--root", str(root), "--config", str(destination)]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    config = Config.load(destination)
    assert config.roots == (root,)
    assert config.backend == "mlx" and config.dimensions == 512
    assert config.enable_documents and config.enable_images
    assert not config.enable_audio and not config.enable_video
    assert config.model_path.is_relative_to(config.storage_path)
    assert config.database.is_relative_to(config.storage_path)
    assert not config.database.exists()
    original = destination.read_bytes()
    second = subprocess.run(command, capture_output=True, text=True)
    assert second.returncode != 0 and destination.read_bytes() == original
    assert "already exists" in second.stderr


def test_configure_rejects_missing_roots_without_writing(tmp_path):
    destination = tmp_path / "settings.toml"
    script = Path(__file__).resolve().parents[1] / "scripts/configure.py"
    result = subprocess.run([sys.executable, str(script), "--root", str(tmp_path / "missing"),
                             "--config", str(destination)], capture_output=True, text=True)
    assert result.returncode != 0 and not destination.exists()
