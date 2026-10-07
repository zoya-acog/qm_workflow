#!/bin/bash
# Self-healing container watchdog for advqm-voila.
#
# own3 (the host this runs on) reboots periodically without warning, and the
# docker run command carries no restart policy that survives that — Docker
# containers aren't guaranteed to be recreated on reboot even with
# --restart=unless-stopped if the container/image storage itself didn't
# survive the reboot. Rather than chase the exact cause each time, this just
# checks on a schedule (via cron, see install_cron.sh) and recreates
# everything from scratch if it's missing.
#
# Idempotent: safe to run repeatedly, does nothing if the container is
# already up and running.

set -euo pipefail

REPO_DIR="/mnt/own6d/qe_workflow/repo"
IMAGE="advqm-voila"
CONTAINER="advqm-voila"

cd "$REPO_DIR"

# Already running? Nothing to do.
if [ -n "$(docker ps -q --filter "name=^${CONTAINER}\$" --filter status=running)" ]; then
    exit 0
fi

echo "[$(date -Is)] advqm-voila not running — recreating"

# Clear out any stopped/dangling container with this name first.
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

# Rebuild the image only if it's missing (avoids an unnecessary rebuild on
# every watchdog tick when nothing has changed).
if [ -z "$(docker images -q "$IMAGE" 2>/dev/null)" ]; then
    echo "[$(date -Is)] image $IMAGE missing — building"
    docker build -t "$IMAGE" .
fi

docker run -d --name "$CONTAINER" \
  --restart=unless-stopped \
  -v "/mnt/own6d/qe_workflow/data:/mnt/own6d/qe_workflow/data" \
  -v /run/munge:/run/munge \
  --user "7158:6006" -e HOME=/tmp \
  --label hostname="advqm-voila" --label port="8866" --label user="zoya@aganitha.ai" --label security=none \
  --label description="advQMcalc_test QM Crystal Workflow Voila" \
  "$IMAGE"

echo "[$(date -Is)] advqm-voila recreated"
