IMAGE ?= advqm-voila
CONTAINER ?= advqm-voila
DATA_DIR ?= /mnt/own6d/qe_workflow/data

.DEFAULT_GOAL := help
.PHONY: help build up rebuild down restart status logs

help:
	@printf '%s\n' \
	  'make build    Build the Docker image' \
	  'make up       Replace the named container using the current image' \
	  'make rebuild  Build the image, then replace the container' \
	  'make down     Stop and remove the container' \
	  'make restart  Restart the existing container' \
	  'make status   Show container status' \
	  'make logs     Follow the last 100 log lines'

build:
	docker build -t "$(IMAGE)" .

# Replaces only the container; persistent workflow data stays in DATA_DIR.
up:
	@docker rm -f "$(CONTAINER)" >/dev/null 2>&1 || true
	docker run -d --name "$(CONTAINER)" --restart=unless-stopped \
	  -v "$(DATA_DIR):/mnt/own6d/qe_workflow/data" \
	  -v /run/munge:/run/munge \
	  --user 7158:6006 -e HOME=/tmp \
	  --label hostname=advqm-voila --label port=8866 --label user=zoya@aganitha.ai --label security=none \
	  --label 'description=advQMcalc_test QM Crystal Workflow Voila' \
	  "$(IMAGE)"

rebuild: build
	$(MAKE) up IMAGE="$(IMAGE)" CONTAINER="$(CONTAINER)" DATA_DIR="$(DATA_DIR)"

down:
	@docker rm -f "$(CONTAINER)" >/dev/null 2>&1 || true

restart:
	docker restart "$(CONTAINER)"

status:
	docker ps -a --filter "name=^$(CONTAINER)$$" --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

logs:
	docker logs --tail 100 -f "$(CONTAINER)"
