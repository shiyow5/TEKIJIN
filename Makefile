.PHONY: help setup setup-backend setup-ml setup-frontend \
        fmt fmt-backend fmt-frontend \
        fmt-check fmt-check-backend fmt-check-frontend \
        lint lint-backend lint-frontend \
        test test-backend test-frontend e2e \
        run-backend run-frontend serve dev serve-prod \
        db-up db-down seed migrate embed eval \
        typecheck-frontend check clean

# ============================================================
# .env handling
# ============================================================
# .env is intentionally NOT parsed by Make: dotenv values can contain Make
# metacharacters and secrets, and Make-parsing them is fragile/unsafe. The
# backend reads <repo>/.env directly via pydantic-settings; docker-compose and
# Next.js read their own env. Export vars in your shell if a target needs them.

# ============================================================
# Variables
# ============================================================
BACKEND_DIR  := backend
FRONTEND_DIR := frontend
PY           ?= python3

# ============================================================
# Help
# ============================================================
help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

# ============================================================
# Setup
# ============================================================
setup: setup-backend setup-frontend ## Install all dev dependencies

setup-backend: ## Install backend runtime + dev deps (CI-light; NO real embedder)
	cd $(BACKEND_DIR) && $(PY) -m pip install -r requirements.txt -r requirements-dev.txt

setup-ml: ## Install the heavy ML deps (sentence-transformers/torch) for the real embedder
	# Required to RUN the API/agent with the default SentenceTransformer embedder
	# (tests use a FakeEmbedder and do NOT need this). See requirements-ml.txt.
	cd $(BACKEND_DIR) && $(PY) -m pip install -r requirements-ml.txt

setup-frontend: ## Install frontend dev tooling (biome, vitest)
	cd $(FRONTEND_DIR) && npm install

# ============================================================
# Format
# ============================================================
fmt: fmt-backend fmt-frontend ## Format backend and frontend

fmt-backend: ## Format Python with ruff
	cd $(BACKEND_DIR) && $(PY) -m ruff format .

fmt-frontend: ## Format frontend with biome
	cd $(FRONTEND_DIR) && npm run format

fmt-check: fmt-check-backend fmt-check-frontend ## Check formatting without modifying files

fmt-check-backend: ## Check Python formatting
	cd $(BACKEND_DIR) && $(PY) -m ruff format --check .

fmt-check-frontend: ## Check frontend formatting
	cd $(FRONTEND_DIR) && npm run format:check

# ============================================================
# Lint
# ============================================================
lint: lint-backend lint-frontend ## Run all linters

lint-backend: ## Lint Python with ruff
	cd $(BACKEND_DIR) && $(PY) -m ruff check .

lint-frontend: ## Lint frontend with biome
	cd $(FRONTEND_DIR) && npm run lint

# ============================================================
# Test
# ============================================================
test: test-backend test-frontend ## Run all tests

test-backend: ## Run backend tests (pytest)
	cd $(BACKEND_DIR) && $(PY) -m pytest

test-frontend: ## Run frontend unit tests (vitest)
	cd $(FRONTEND_DIR) && npm test

typecheck-frontend: ## Type-check the frontend (tsc)
	cd $(FRONTEND_DIR) && npm run typecheck

e2e: ## Run Playwright end-to-end tests (frontend; builds + serves the app itself)
	# First run needs the browser: `cd frontend && npx playwright install chromium`
	# (CI uses `--with-deps`). The suite mocks all backend traffic — no live API.
	cd $(FRONTEND_DIR) && npm run e2e

# ============================================================
# Run
# ============================================================
run-backend: ## Run only the backend dev server (uvicorn, auto-reload; stub LLM, MemorySaver)
	cd $(BACKEND_DIR) && $(PY) -m uvicorn tekijin.main:app --reload --app-dir src

run-frontend: ## Run only the frontend dev server (Next.js, :3000)
	cd $(FRONTEND_DIR) && npm run dev

# `make serve` / `make dev`: one-command full-stack dev launcher.
#
# Runs the auto-reloading backend and the Next.js dev server together, streaming
# both logs to this terminal. LLM (C1/C2/C7) and the checkpointer are STUBBED by
# default, so the servers boot with NO vLLM / external LLM. NOTE: to actually
# process a question you still need Postgres with seeded, embedded data —
# `make db-up seed` and `make setup-ml embed`; without them the UI loads and both
# servers run, but submitting a question errors.
#
# Teardown (needs bash): `set -m` puts each server in its OWN process group, so
# the trap / epilogue kill exactly those two groups (backend reloader + node
# child included) — Ctrl-C in THIS terminal stops both with no orphan. `wait -n`
# returns as soon as EITHER server exits, so if one dies at startup (e.g. port in
# use) the peer is stopped and the failing status is propagated instead of
# hanging. (Signalling only the top-level make PID out-of-band — not its group —
# is a Make limitation that can still orphan the servers.)
serve: ## Run backend (:8000) + frontend (:3000) together for local dev; Ctrl-C stops both
	@echo ">> backend  http://localhost:8000  (docs: /docs)"
	@echo ">> frontend http://localhost:3000"
	@echo ">> Ctrl-C stops both. (LLM/checkpointer stubbed; DB+embeddings needed to answer)"
	@bash -c 'set -m; \
		( cd $(BACKEND_DIR) && exec $(PY) -m uvicorn tekijin.main:app --reload --app-dir src ) & back=$$!; \
		( cd $(FRONTEND_DIR) && exec npm run dev ) & front=$$!; \
		stop() { \
			trap - INT TERM EXIT; \
			kill -TERM -- -$$back -$$front 2>/dev/null; \
			for _ in 1 2 3 4 5 6 7 8 9 10; do \
				kill -0 $$back 2>/dev/null || kill -0 $$front 2>/dev/null || break; \
				sleep 0.5; \
			done; \
			kill -KILL -- -$$back -$$front 2>/dev/null; \
			wait 2>/dev/null; \
		}; \
		trap stop INT TERM EXIT; \
		wait -n; status=$$?; stop; exit $$status'

dev: serve ## Alias for `make serve` (start the full-stack dev environment)

serve-prod: ## Run the backend against real vLLM + PostgresSaver (production-like, backend only)
	# Needs the ML deps (make setup-ml) for the embedder. Point TEKIJIN_LLM_BASE_URL
	# at your vLLM /v1 endpoint and TEKIJIN_DATABASE_URL at Postgres, then:
	#   TEKIJIN_LLM_BACKEND=vllm TEKIJIN_CHECKPOINTER_BACKEND=postgres make serve-prod
	# SINGLE WORKER ONLY: the session dispatch registry is in-process, so do NOT
	# add --workers (a durable/sticky multi-worker queue is a separate issue).
	cd $(BACKEND_DIR) && \
		TEKIJIN_LLM_BACKEND=$${TEKIJIN_LLM_BACKEND:-vllm} \
		TEKIJIN_CHECKPOINTER_BACKEND=$${TEKIJIN_CHECKPOINTER_BACKEND:-postgres} \
		$(PY) -m uvicorn tekijin.main:app --host 0.0.0.0 --port 8000 --workers 1 --app-dir src

# ============================================================
# Database
# ============================================================
db-up: ## Start the local PostgreSQL 16 + pgvector container (waits until healthy)
	# --wait blocks until the compose healthcheck (pg_isready) passes, so a
	# following `make seed` does not race the database's first-run init.
	docker compose up -d --wait db

db-down: ## Stop the local PostgreSQL container
	docker compose down

seed: ## Seed the database from the synthetic fixtures (DESTRUCTIVE: truncates first)
	cd $(BACKEND_DIR) && PYTHONPATH=src $(PY) -m tekijin.data.seed

migrate: ## Apply non-destructive schema migrations (keeps data; re-run `make embed` after)
	cd $(BACKEND_DIR) && PYTHONPATH=src $(PY) -m tekijin.data.migrate

embed: ## Compute + store dense embeddings (needs requirements-ml.txt + a real model)
	PYTHONPATH=$(BACKEND_DIR)/src $(PY) scripts/embed_fixtures.py

eval: ## Run the offline evaluation (Top-1/Recall@3/MRR/route); run `make seed embed` first
	cd $(BACKEND_DIR) && PYTHONPATH=src $(PY) -m tekijin.eval

# ============================================================
# Aggregate
# ============================================================
check: fmt-check lint test ## Run format check, lint, and tests

clean: ## Remove caches and build artifacts
	rm -rf $(BACKEND_DIR)/.pytest_cache $(BACKEND_DIR)/.ruff_cache
	find $(BACKEND_DIR) -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf $(FRONTEND_DIR)/node_modules $(FRONTEND_DIR)/coverage
