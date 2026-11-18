#!/usr/bin/env bash
# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
#
# Lokaler Entwicklungsstart, ohne Docker. Beispiele:
#   ./run.sh                  Dashboard/API auf http://127.0.0.1:8095
#   ./run.sh ingest           Logs aus LOG_DIR einlesen
#   ./run.sh ingest --rebuild
#   ./run.sh fetch            Logs per SFTP holen + Ingest
#   ./run.sh fetch --dry-run
#   ./run.sh retention --days 30
#
# .env wird geladen (SFTP-Zugang, Vhost-Aliase), aber LOG_DIR/DB_PATH bleiben
# lokale Host-Pfade statt der Container-Pfade. Der taegliche Fetch-Scheduler ist
# beim Serverstart bewusst aus, damit der Dev-Server keinen Fetch ausloest.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo ".venv fehlt. Zuerst:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

_env_log="${LOG_DIR:-}"
_env_db="${DB_PATH:-}"
_env_sched="${FETCH_SCHEDULE:-}"
_env_startup="${FETCH_ON_STARTUP:-}"
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

export LOG_DIR="${_env_log:-$PWD/data/logs}"
export DB_PATH="${_env_db:-$PWD/data/weblog.sqlite}"

cmd="${1:-serve}"
if [ $# -gt 0 ]; then shift; fi

case "$cmd" in
  serve)
    export FETCH_SCHEDULE="${_env_sched:-}"
    export FETCH_ON_STARTUP="${_env_startup:-false}"
    exec .venv/bin/python -m app "$@"
    ;;
  ingest)
    exec .venv/bin/python -m app.ingest "$@"
    ;;
  fetch)
    exec .venv/bin/python -m app.fetch "$@"
    ;;
  retention)
    exec .venv/bin/python -m app.retention "$@"
    ;;
  *)
    exec .venv/bin/python -m app "$cmd" "$@"
    ;;
esac
