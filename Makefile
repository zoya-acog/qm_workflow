PYTHON ?= python3
VENV ?= .venv
PY := $(VENV)/bin/python
PORT ?= 8866
CONFIG ?= config.yaml
VERSION := $(shell git describe --tags --always --dirty 2>/dev/null || echo unknown)

.DEFAULT_GOAL := help
.PHONY: help install build test run clean distclean version

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Create .venv, install the app, backend and test dependencies, register widgets and kernel
	@$(PYTHON) -c "import sys; sys.exit(sys.version_info < (3, 11))" || { echo "Python 3.11 or newer is required ($(PYTHON) is $$($(PYTHON) -V 2>&1)); run: make install PYTHON=/path/to/python3.11"; exit 1; }
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt
	$(VENV)/bin/jupyter nbextension install --sys-prefix --py ipyvuetify --overwrite >/dev/null
	$(VENV)/bin/jupyter nbextension enable --sys-prefix --py ipyvuetify >/dev/null
	$(VENV)/bin/jupyter nbextension install --sys-prefix --py widgetsnbextension --overwrite >/dev/null
	$(VENV)/bin/jupyter nbextension enable --sys-prefix --py widgetsnbextension >/dev/null
	$(PY) -m ipykernel install --sys-prefix --name python3 --display-name "Python 3" >/dev/null

build: ## Byte-compile the app and backend (catches syntax errors)
	$(PY) -m compileall -q app advQMcalc_test/src

test: ## Run all tests
	$(PY) -m pytest -q tests

run: ## Run the web app on 127.0.0.1:PORT with the site settings in CONFIG (config.yaml)
	@test -f "$(CONFIG)" || { echo "No $(CONFIG): copy config.example.yaml to config.yaml and fill it in (docs/installation.md, steps 3-4)."; exit 1; }
	@# .venv/bin first on PATH: the backend runs cif2cell by name.
	PATH="$(abspath $(VENV))/bin:$$PATH" ADVQM_SITE_CONFIG="$(abspath $(CONFIG))" $(VENV)/bin/voila --port=$(PORT) --Voila.ip=127.0.0.1 --no-browser --autoreload=False \
	  --VoilaConfiguration.enable_nbextensions=True \
	  "--Voila.tornado_settings={'websocket_ping_interval': 20}" \
	  "--VoilaConfiguration.file_whitelist=['.*\\.(png|jpg|gif|svg)', 'downloads/qe_output_files_[0-9a-f]{32}\\.zip', 'downloads/run_logs_[0-9a-f]{32}\\.zip']" \
	  app/app.ipynb

clean: ## Remove caches and generated downloads
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache app/downloads/*.zip

distclean: clean ## Also remove .venv
	rm -rf $(VENV)

version: ## Print the version a build would carry
	@echo $(VERSION)
