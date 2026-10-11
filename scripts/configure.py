"""Create local settings from explicit folders without overwriting an installation."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tinysearch.config import Config
from tinysearch.settings import CATALOG, configured
from tinysearch.storage import APP_SUPPORT


def main():
    parser = argparse.ArgumentParser(description="Choose folders for local semantic search")
    parser.add_argument("--root", action="append", required=True, help="Folder to index; repeat to add more folders")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    args = parser.parse_args()
    destination = args.config.expanduser().resolve()
    if destination.exists():
        parser.error("Configuration already exists. Edit it locally instead of overwriting it.")
    roots = tuple(sorted({Path(value).expanduser().resolve() for value in args.root}))
    if any(not root.is_dir() for root in roots):
        parser.error("Each root must be an existing folder.")
    model = next(item for item in CATALOG if item.get("recommended"))
    initial = Config(roots, APP_SUPPORT / model["path"], model["revision"],
                     APP_SUPPORT / "data/index.sqlite3", source_path=destination,
                     storage_path=APP_SUPPORT)
    selected = configured(initial, dict(model_variant=model["id"], dimensions=512,
        enable_documents=True, enable_images=True, enable_video=False, enable_audio=False,
        thumbnail_px=256, chunk_tokens=512, idle_seconds=60))
    destination.parent.mkdir(parents=True, exist_ok=True)
    selected.save()
    print("Local configuration created. Start the service, then download the model in Model Settings.")


if __name__ == "__main__":
    main()
