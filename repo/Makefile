IMAGE ?= advqm-voila
CONTAINER ?= advqm-voila
DATA_DIR ?= /mnt/own6d/qe_workflow/data
VENV ?= .venv
PY := $(VENV)/bin/python
PORT ?= 8866
VERSION := $(shell git describe --tags --always --dirty 2>/dev/null || echo unknown)

.DEFAULT_GOAL := help
.PHONY: help install skills-install build test run clean distclean version \
        docker-build up rebuild down restart status logs

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# --- Local (no Docker): the way a site without Docker runs the app -----------

install: ## Create .venv and install the app, backend and test dependencies
	python3 -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt
	$(VENV)/bin/jupyter nbextension install --sys-prefix --py ipyvuetify --overwrite >/dev/null
	$(VENV)/bin/jupyter nbextension enable --sys-prefix --py ipyvuetify >/dev/null
	$(VENV)/bin/jupyter nbextension install --sys-prefix --py widgetsnbextension --overwrite >/dev/null
	$(VENV)/bin/jupyter nbextension enable --sys-prefix --py widgetsnbextension >/dev/null

skills-install: ## Restore project skills from skills-pack.lock.json
	skills-pack update; skills-pack upgrade

build: ## Byte-compile the app and backend (catches syntax errors)
	$(PY) -m compileall -q app advQMcalc_test/src

test: ## Run all tests
	$(PY) -m pytest -q tests

run: ## Run the web app on 127.0.0.1:PORT (site settings from ADVQM_SITE_CONFIG)
	$(VENV)/bin/voila --port=$(PORT) --Voila.ip=127.0.0.1 --no-browser --autoreload=False \
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

# --- Docker (our cluster): the app runs in a container on own3 ---------------

docker-build: ## Build the app Docker image
	docker build -t "$(IMAGE)" .

# Replaces only the container; persistent workflow data stays in DATA_DIR.
up: ## Replace the running container with one from the current image
	@docker rm -f "$(CONTAINER)" >/dev/null 2>&1 || true
	docker run -d --name "$(CONTAINER)" --restart=unless-stopped \
	  -v "$(DATA_DIR):/mnt/own6d/qe_workflow/data" \
	  -v /run/munge:/run/munge \
	  --user 7158:6006 -e HOME=/tmp \
	  --label hostname=advqm-voila --label port=8866 --label user=zoya@aganitha.ai \
	  --label 'description=advQMcalc_test QM Crystal Workflow Voila' \
	  "$(IMAGE)"

rebuild: docker-build ## Build the image, then replace the container
	$(MAKE) up IMAGE="$(IMAGE)" CONTAINER="$(CONTAINER)" DATA_DIR="$(DATA_DIR)"

down: ## Stop and remove the container
	@docker rm -f "$(CONTAINER)" >/dev/null 2>&1 || true

restart: ## Restart the existing container
	docker restart "$(CONTAINER)"

status: ## Show container status
	docker ps -a --filter "name=^$(CONTAINER)$$" --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

logs: ## Follow the last 100 container log lines
	docker logs --tail 100 -f "$(CONTAINER)"
