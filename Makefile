PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: help venv fetch build data clean-derived clean-cache

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n", $$1, $$2}'

venv: ## create .venv and install deps
	python3 -m venv .venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements.txt

fetch: ## stage 1 — download sources into data/cache/ (network; NHD is slow)
	$(PY) -u src/fetch.py

build: ## stage 2 — reconcile and validate into data/derived/ (offline)
	$(PY) -u src/build.py

data: fetch build ## run both data stages

clean-derived: ## drop derived output, keep the download cache
	rm -rf data/derived/*

clean-cache: ## drop the download cache; next fetch re-downloads everything
	rm -rf data/cache/*
