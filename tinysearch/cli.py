import argparse
import json
from pathlib import Path
import urllib.request
from .config import Config


def main():
    parser = argparse.ArgumentParser(description="Local semantic search")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    sub.add_parser("index")
    sub.add_parser("status")
    query = sub.add_parser("search")
    query.add_argument("query")
    query.add_argument("--kind", choices=["all", "document", "image", "video", "audio"], default="all")
    args = parser.parse_args()
    config = Config.load(args.config)
    if args.command == "serve":
        from .storage import migrate
        config = migrate(config, Path(__file__).resolve().parents[1])
        import uvicorn
        from .server import create_app
        uvicorn.run(create_app(config), host="127.0.0.1", port=config.port, access_log=False)
        return
    body = None
    if args.command == "index":
        body = {}
    elif args.command == "search":
        body = {"query": args.query, "kind": args.kind}
    request = urllib.request.Request(f"http://127.0.0.1:{config.port}/v1/{args.command}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=3600) as response:
            data = json.load(response)
            for result in data.get("results", []):
                result.pop("thumbnail", None)
            print(json.dumps(data, ensure_ascii=False, indent=2))
    except urllib.error.URLError as error:
        parser.exit(1, f"Search service unavailable: {error}\nStart: .venv/bin/python -m tinysearch serve\n")


if __name__ == "__main__":
    main()
