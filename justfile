# Retinue task runner (§5). Requires: uv, pnpm (via corepack), just.

set shell := ["bash", "-cu"]

default:
    @just --list

# one-time dev setup
setup:
    uv venv .venv --python 3.12
    uv pip install -p .venv/bin/python -e "./backend[dev]"
    cd frontend && corepack pnpm install

# backend API with reload + vite dev server (run in two terminals, or use a mux)
dev-api:
    RETINUE_MODELS__MOCK_ENABLED=1 .venv/bin/uvicorn retinue.app:create_app --factory --reload --port 8000 --app-dir backend/src

dev-web:
    cd frontend && corepack pnpm run dev

test: test-backend test-frontend

test-backend:
    cd backend && ../.venv/bin/python -m pytest tests/ -q

test-frontend:
    cd frontend && corepack pnpm run test

lint:
    cd backend && ../.venv/bin/ruff check src tests && ../.venv/bin/ruff format --check src tests
    cd frontend && corepack pnpm run typecheck

fmt:
    cd backend && ../.venv/bin/ruff format src tests && ../.venv/bin/ruff check --fix src tests

# build the SPA, embed it, produce the wheel in ./dist
build:
    cd frontend && corepack pnpm run build
    .venv/bin/python scripts/build_frontend_into_wheel.py --skip-build
    uv build backend --wheel --sdist -o dist

# regenerate the OpenAPI snapshot the TS client is checked against (D24)
openapi:
    .venv/bin/python scripts/gen_ts_client.py

serve:
    .venv/bin/retinue serve

doctor:
    .venv/bin/retinue doctor
