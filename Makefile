.PHONY: help setup setup-backend setup-frontend \
        fmt fmt-backend fmt-frontend \
        fmt-check fmt-check-backend fmt-check-frontend \
        lint lint-backend lint-frontend \
        test test-backend test-frontend \
        run-backend \
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
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

# ============================================================
# Setup
# ============================================================
setup: setup-backend setup-frontend ## Install all dev dependencies

setup-backend: ## Install backend runtime + dev dependencies
	cd $(BACKEND_DIR) && $(PY) -m pip install -r requirements.txt -r requirements-dev.txt

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

# ============================================================
# Run
# ============================================================
run-backend: ## Run the backend dev server (uvicorn, auto-reload)
	cd $(BACKEND_DIR) && $(PY) -m uvicorn tekijin.main:app --reload --app-dir src

# ============================================================
# Aggregate
# ============================================================
check: fmt-check lint test ## Run format check, lint, and tests

clean: ## Remove caches and build artifacts
	rm -rf $(BACKEND_DIR)/.pytest_cache $(BACKEND_DIR)/.ruff_cache
	find $(BACKEND_DIR) -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf $(FRONTEND_DIR)/node_modules $(FRONTEND_DIR)/coverage
