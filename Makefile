.PHONY: install dev test lint seed mcp
install:
	python -m pip install -e ".[dev]"
dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
test:
	pytest -q
lint:
	ruff check app tests
seed:
	python -m app.seed
mcp:
	python -m app.mcp_server
