.PHONY: install test lint sandbox doctor demo benchmark serve

install:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .

sandbox:  ## build the isolated image that runs linters/tests on PR code
	docker build -f docker/sandbox.Dockerfile -t code-review-sandbox:latest .

doctor:
	python -m app doctor

demo:
	python -m app demo

benchmark:
	python -m benchmark

serve:
	python -m app serve
