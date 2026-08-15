PYTHON ?= python3

.PHONY: init demo frontend backend test

init:
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -r backend/requirements.txt
	npm install

demo:
	PYTHONPATH=backend .venv/bin/uvicorn app.main:app --port 8000 & backend_pid=$$!; \
	trap 'kill $$backend_pid 2>/dev/null || true' EXIT INT TERM; \
	npm run dev

frontend:
	npm run dev

backend:
	PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload --port 8000

test:
	PYTHONPATH=backend $(PYTHON) -m unittest discover -s backend/tests -v
	npm run build
