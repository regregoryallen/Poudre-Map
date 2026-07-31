PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: help venv vendor fetch build data qa tiles serve deploy all clean-derived clean-cache

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

vendor: ## re-fetch the browser libs into web/vendor (not tracked in git)
	mkdir -p web/vendor
	curl -sL -o web/vendor/maplibre-gl.js  https://unpkg.com/maplibre-gl@5.9.0/dist/maplibre-gl.js
	curl -sL -o web/vendor/maplibre-gl.css https://unpkg.com/maplibre-gl@5.9.0/dist/maplibre-gl.css
	curl -sL -o web/vendor/pmtiles.js      https://unpkg.com/pmtiles@4.3.0/dist/pmtiles.js

qa: ## unstyled diagnostic plot → out/qa.png
	$(PY) -u src/qa.py

tiles: ## stage 3a — pack derived GeoJSON into web/poudre.pmtiles
	$(PY) -u src/tiles.py

serve: ## local preview with range support, mounted like production
	$(PY) src/devserve.py --mount /poudremap

deploy: ## rsync web/ to homeweb.lan/poudremap and verify
	./deploy.sh

all: data tiles ## rebuild everything from cache through tiles

clean-derived: ## drop derived output, keep the download cache
	rm -rf data/derived/*

clean-cache: ## drop the download cache; next fetch re-downloads everything
	rm -rf data/cache/*
