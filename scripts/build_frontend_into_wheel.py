#!/usr/bin/env python3
"""Build the SPA and copy it into the Python package (§5, §21).

    python scripts/build_frontend_into_wheel.py [--skip-build]

CI runs this before `hatch build`/`uv build`; the wheel then ships the UI at
retinue/static (forced in via [tool.hatch.build] artifacts even though the
built assets are gitignored).
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
STATIC = ROOT / "backend" / "src" / "retinue" / "static"


def run(command: list[str], cwd: Path) -> None:
    print(f"$ {' '.join(command)}  (cwd={cwd})")
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-build", action="store_true", help="reuse frontend/dist instead of rebuilding"
    )
    args = parser.parse_args()

    if not args.skip_build:
        pnpm = shutil.which("pnpm") or shutil.which("corepack")
        if pnpm is None:
            print("error: pnpm (or corepack) is required to build the frontend", file=sys.stderr)
            return 1
        prefix = [pnpm] if pnpm.endswith("pnpm") else [pnpm, "pnpm"]
        run([*prefix, "install", "--frozen-lockfile"], cwd=FRONTEND)
        run([*prefix, "run", "build"], cwd=FRONTEND)

    if not (DIST / "index.html").is_file():
        print(f"error: no build output at {DIST}", file=sys.stderr)
        return 1

    if STATIC.exists():
        shutil.rmtree(STATIC)
    shutil.copytree(DIST, STATIC)
    file_count = sum(1 for p in STATIC.rglob("*") if p.is_file())
    print(f"copied {file_count} files -> {STATIC.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
