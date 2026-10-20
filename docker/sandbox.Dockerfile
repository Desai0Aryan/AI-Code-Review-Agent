# Image used to run linters and tests on untrusted PR code.
# Build:  docker build -f docker/sandbox.Dockerfile -t code-review-sandbox .
FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
RUN pip install ruff bandit mypy pytest

# Containers are started read-only, as uid 65534, with no network and all caps dropped
# (see DockerSandbox in app/sandbox.py). Nothing else is installed on purpose.
USER 65534:65534
WORKDIR /work
