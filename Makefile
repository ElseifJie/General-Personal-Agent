PY ?= .venv/bin/python
PYTHON ?= 3.12
DB ?= postgresql://demo:demo@127.0.0.1:5432/general_personal_agent
PORT ?= 8100

.PHONY: install run dev test lint fmt smoke smoke-gpa e2e e2e-voice-gpa e2e-task-gpa e2e-artifact-tos mcp-server service-token db db-migrate build-wb recover clean
TASK ?=
TENANT ?= demo
SUBJECT ?= local-worker
USERS ?= alice

UV_CACHE_DIR ?= .uv-cache
UV_PYTHON_INSTALL_DIR ?= .uv-python

install:
	UV_CACHE_DIR=$(UV_CACHE_DIR) UV_PYTHON_INSTALL_DIR=$(UV_PYTHON_INSTALL_DIR) uv venv .venv --python $(PYTHON)
	UV_CACHE_DIR=$(UV_CACHE_DIR) UV_PYTHON_INSTALL_DIR=$(UV_PYTHON_INSTALL_DIR) uv pip install --python $(PY) -e ".[dev]"

run:
	$(PY) -m uvicorn general_personal_agent.main:app --host 127.0.0.1 --port $(PORT)

dev:
	$(PY) -m uvicorn general_personal_agent.main:app --reload --host 127.0.0.1 --port $(PORT)

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests scripts

fmt:
	$(PY) -m ruff format src tests scripts
	$(PY) -m ruff check --fix src tests scripts

db:
	psql "$(DB)" -f db/schema.sql
	psql "$(DB)" -f db/migrations/002_v05.sql
	psql "$(DB)" -f db/migrations/003_wb.sql
	psql "$(DB)" -f db/migrations/004_voice_workbench.sql
	psql "$(DB)" -f db/migrations/005_gpa_core.sql
	psql "$(DB)" -f db/migrations/006_gpa_context.sql
	psql "$(DB)" -f db/migrations/007_drop_legacy.sql
	psql "$(DB)" -f db/migrations/008_identity_convergence.sql
	psql "$(DB)" -f db/migrations/009_artifact_retention.sql
	psql "$(DB)" -f db/migrations/010_service_token_revocation.sql
	psql "$(DB)" -f db/migrations/011_task_idempotency_scope.sql
	psql "$(DB)" -f db/migrations/012_conversation_management.sql
	psql "$(DB)" -f db/migrations/013_voice_session_model.sql
	psql "$(DB)" -f db/migrations/014_files.sql
	psql "$(DB)" -f db/migrations/015_reliability_fixes.sql
	psql "$(DB)" -f db/migrations/016_gc_outbox_recovery.sql
	psql "$(DB)" -f db/migrations/017_task_partial_status.sql
	psql "$(DB)" -f db/migrations/018_task_retry_backoff.sql

db-migrate:
	@set -eu; \
	applied=$$(psql "$(DB)" -Atc "select version from schema_migrations" 2>/dev/null || true); \
	for f in db/migrations/*.sql; do \
		v=$$(basename "$$f" .sql); \
		case "$$v" in 002_v05) continue;; esac; \
		case "$$v" in \
			003_wb|004_voice_workbench) \
				if printf '%s\n' "$$applied" | grep -qx "005_gpa_core"; then continue; fi;; \
		esac; \
		if printf '%s\n' "$$applied" | grep -qx "$$v"; then continue; fi; \
		echo "applying $$f"; \
		psql "$(DB)" -v ON_ERROR_STOP=1 -f "$$f"; \
	done

build-wb:
	cd web-workbench && npm ci && npm run build

smoke:
	@set -e; for s in smoke_db smoke_ark smoke_ov smoke_tts smoke_asr smoke_ma smoke_tos verify_isolation; do \
		echo "===== $$s ====="; $(PY) -m scripts.$$s; \
	done

smoke-gpa:
	$(PY) -m scripts.smoke_gpa

e2e:
	$(PY) -m scripts.e2e_voice_gpa
	$(PY) -m scripts.e2e_task_gpa

e2e-voice-gpa:
	$(PY) -m scripts.e2e_voice_gpa

e2e-task-gpa:
	$(PY) -m scripts.e2e_task_gpa

e2e-artifact-tos:
	$(PY) -m scripts.e2e_artifact_tos

mcp-server:
	$(PY) -m scripts.task_mcp_server --host 127.0.0.1 --port 8201

service-token:
	$(PY) -m scripts.issue_service_token --tenant $(TENANT) --subject $(SUBJECT) --users $(USERS)

test-wb-ui:
	cd web-workbench && npx playwright test

clean:
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
