#!/bin/sh
# Start, stop or inspect the local metrics stack (grafana/otel-lgtm). Data lives in telemetry/data (gitignored).
# Ports are uncommon on purpose and bound to localhost only: Grafana 47430, OTLP gRPC 47317, OTLP HTTP 47318.
set -eu
NAME=megavibe-metrics
ROOT=$(cd "$(dirname "$0")" && pwd)
GRAFANA=${METRICS_GRAFANA_PORT:-47430}
GRPC=${METRICS_OTLP_GRPC_PORT:-47317}
HTTP=${METRICS_OTLP_HTTP_PORT:-47318}
PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:/Applications/OrbStack.app/Contents/MacOS/xbin"
command -v docker >/dev/null 2>&1 || { echo "docker not found" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "docker is not running" >&2; exit 1; }
running() { [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || true)" = true ]; }
case "${1:-status}" in
  start)
    running && exit 0
    if docker inspect "$NAME" >/dev/null 2>&1; then docker start "$NAME" >/dev/null; exit 0; fi
    mkdir -p "$ROOT/data"
    docker run -d --name "$NAME" -p "127.0.0.1:$GRAFANA:3000" -p "127.0.0.1:$GRPC:4317" -p "127.0.0.1:$HTTP:4318" \
      -v "$ROOT/data:/data" -v "$ROOT/dashboard.json:/megavibe/dashboard.json:ro" \
      -v "$ROOT/provisioning/dashboards.yaml:/otel-lgtm/grafana/conf/provisioning/dashboards/megavibe.yaml:ro" grafana/otel-lgtm >/dev/null ;;
  stop) running && docker stop "$NAME" >/dev/null || true ;;
  remove) docker rm -f "$NAME" >/dev/null 2>&1 || true ;;
  status) if running; then echo "running http://127.0.0.1:$GRAFANA"; else echo stopped; exit 3; fi ;;
  *) echo "usage: $0 start|stop|remove|status" >&2; exit 2 ;;
esac
