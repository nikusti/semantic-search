"""Download only the configured model; keep indexes and user files unchanged."""
import argparse
import json
import os
from pathlib import Path
import sys

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tinysearch.config import Config
from tinysearch.model_source import resolve_source, ensure_downloaded
from tinysearch.settings import storage_root
from tinysearch.storage import migrate


def main():
    parser = argparse.ArgumentParser(description="Download the configured embedding model")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    args = parser.parse_args()
    config = migrate(Config.load(args.config), Path(__file__).resolve().parents[1])
    catalog = json.loads((Path(__file__).resolve().parents[1] / "tinysearch/model_catalog.json").read_text())
    entry = resolve_source(storage_root(config), catalog, config.model_variant, config.model_url)
    ensure_downloaded(storage_root(config), entry)
    print("Configured model is downloaded and ready for local inference.")


if __name__ == "__main__":
    main()
