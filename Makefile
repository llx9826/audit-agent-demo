PYTHON ?= python3

.PHONY: init demo frontend backend describe doctor rag-install rag-offline rag-catalog rag-index rag-catalog-publish rag-build rag-eval rag-answer-eval rag-online-smoke agent-eval agent-eval-baseline agent-live-eval agent-live-smoke feedback-export test verify real-first-run check e2e

init:
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -r backend/requirements.txt
	npm install

rag-install:
	.venv/bin/pip install -r backend/requirements-integrations.txt

demo:
	HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=backend .venv/bin/uvicorn app.main:app --port 8000 & backend_pid=$$!; \
	trap 'kill $$backend_pid 2>/dev/null || true' EXIT INT TERM; \
	npm run dev

frontend:
	npm run dev

backend:
	HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload --port 8000

describe:
	PYTHONPATH=backend:. .venv/bin/python -m app describe

doctor:
	PYTHONPATH=backend:. .venv/bin/python -m app doctor

rag-offline:
	PYTHONPATH=backend .venv/bin/python backend/scripts/build_requirement_chunks.py --profile real --contextualizer model

rag-catalog:
	PYTHONPATH=backend .venv/bin/python backend/scripts/link_requirement_catalog.py

rag-index:
	PYTHONPATH=backend .venv/bin/python backend/scripts/index_requirements.py

rag-catalog-publish:
	PYTHONPATH=backend .venv/bin/python backend/scripts/publish_rag_catalog.py

rag-build: rag-offline rag-catalog rag-index

rag-eval:
	HF_HUB_OFFLINE=1 PYTHONPATH=backend .venv/bin/python backend/scripts/evaluate_requirement_rag.py

rag-answer-eval:
	HF_HUB_OFFLINE=1 PYTHONPATH=backend:backend/scripts:. .venv/bin/python backend/scripts/evaluate_knowledge_answers.py

rag-online-smoke:
	HF_HUB_OFFLINE=1 PYTHONPATH=backend:. .venv/bin/python backend/scripts/smoke_knowledge_rag.py

agent-eval:
	PYTHONPATH=backend:. .venv/bin/python backend/scripts/evaluate_agent_trajectories.py --mode deterministic --trials 3

agent-eval-baseline:
	PYTHONPATH=backend:. .venv/bin/python backend/scripts/evaluate_agent_trajectories.py --mode deterministic --trials 3 --update-baseline

agent-live-eval:
	PYTHONPATH=backend:. .venv/bin/python backend/scripts/evaluate_agent_trajectories.py --mode live --trials 3

feedback-export:
	PYTHONPATH=backend:. .venv/bin/python backend/scripts/export_feedback_dataset.py

agent-live-smoke:
	PYTHONPATH=backend:. .venv/bin/python backend/scripts/smoke_agent_loop.py

test:
	PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
	npm test

check: test rag-eval agent-eval

verify: test rag-eval agent-eval
	npm run lint

real-first-run: doctor
	$(MAKE) rag-online-smoke
	$(MAKE) agent-live-smoke
	$(MAKE) demo

e2e:
	env NODE_PATH=/Users/lilinxuan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules /Users/lilinxuan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node tests/material-audit.e2e.cjs
