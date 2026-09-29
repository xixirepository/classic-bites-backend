#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
command -v docker >/dev/null || { echo 'Docker Engine is required.' >&2; exit 1; }
docker compose version >/dev/null
action="${1:-help}"
if [[ $# -gt 0 ]]; then shift; fi
case "$action" in
  install)
    python3 scripts/init-env.py
    docker compose config --quiet
    docker compose build
    docker compose up -d --wait --wait-timeout 180 mysql minio
    python3 scripts/setup-media-access.py
    docker compose run --rm --no-deps fastapi python migrate_auth.py
    docker compose up -d --wait --wait-timeout 180
    docker compose ps
    ;;
  start)
    docker compose up -d --wait --wait-timeout 180
    ;;
  stop) docker compose stop ;;
  down) docker compose down ;;
  restart)
    docker compose stop
    docker compose up -d --wait --wait-timeout 180
    ;;
  status) docker compose ps -a ;;
  logs) docker compose logs --tail=100 --follow "$@" ;;
  check)
    python3 scripts/smoke.py
    python3 scripts/check-media-api.py
    docker compose exec -T fastapi python < scripts/check-media-limits.py
    ;;
  *) echo 'Usage: ./stack.sh {install|start|stop|down|restart|status|logs [service]|check}' ;;
esac
