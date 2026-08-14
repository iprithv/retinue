#!/usr/bin/env python3
"""Dump the OpenAPI schema snapshot the frontend types are written against.

    python scripts/gen_ts_client.py [--check]

D24 end-state is a fully generated TS client (openapi-ts) enforced in CI; the
v0.1 stepping stone is this committed schema snapshot — CI regenerates it and
fails on drift, so any wire change forces a deliberate frontend update.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "frontend" / "src" / "lib" / "api" / "openapi.json"


def generate() -> bytes:
    import orjson

    from retinue.app import create_app
    from retinue.config import Settings

    app = create_app(Settings(home_dir=ROOT / ".openapi-tmp", log_level="warning"))
    schema = app.openapi()
    return orjson.dumps(schema, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if the snapshot is stale")
    args = parser.parse_args()

    fresh = generate() + b"\n"
    if args.check:
        if not SNAPSHOT.is_file() or SNAPSHOT.read_bytes() != fresh:
            print("OpenAPI snapshot is stale: run `python scripts/gen_ts_client.py`", file=sys.stderr)
            return 1
        print("OpenAPI snapshot is current")
        return 0
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_bytes(fresh)
    print(f"wrote {SNAPSHOT.relative_to(ROOT)} ({len(fresh)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
