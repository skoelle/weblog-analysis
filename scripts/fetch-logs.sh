#!/usr/bin/env bash
# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
#
# Holt neue/geaenderte Access-Logs per SFTP und startet danach den Ingest.
#
# Gedacht fuer Webhoster, die die Logs taeglich kurz nach Mitternacht rotieren
# (beobachtet: 23:58 - 00:02). Fuer einen automatischen Lauf ist 01:30 ein
# guter Zeitpunkt (Puffer nach der Rotation).
#
# Aufruf:
#   scripts/fetch-logs.sh                # holen + ingest
#   scripts/fetch-logs.sh --no-ingest    # nur holen
#   scripts/fetch-logs.sh --dry-run      # nur zeigen, was geladen wuerde
#
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

# Zugangsdaten (SFTP_HOST/SFTP_USER/SFTP_PASS); Default ist die Projekt-.env.
ENV_FILE=${SFTP_ENV_FILE:-$ROOT/.env}

# FETCH_LOG_DIR und REMOTE_DIR koennen in der .env stehen; Defaults setzt main().
STATE_DIR=${XDG_STATE_HOME:-$HOME/.local/state}/weblog-analysis
MANIFEST=$STATE_DIR/fetch-manifest.tsv
LOCK=$STATE_DIR/fetch.lock

COMPOSE_FILE=$ROOT/docker-compose.yml

DO_INGEST=1
DRY_RUN=0

CURL=(curl --silent --show-error --connect-timeout 20 --max-time 300
      --retry 2 --retry-delay 3 --retry-connrefused)

NETRC_DIR=""
cleanup() { [ -n "$NETRC_DIR" ] && rm -rf "$NETRC_DIR"; }
trap cleanup EXIT

info() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { printf '[%s] fehler: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Aufruf: scripts/fetch-logs.sh [Optionen]

Optionen:
  --no-ingest     nach dem Download keinen Ingest starten
  --dry-run, -n   nur anzeigen, was geladen wuerde
  -h, --help      diese Hilfe

Umgebung:
  SFTP_ENV_FILE   Datei mit SFTP_HOST/SFTP_USER/SFTP_PASS (Default: .env)
  FETCH_LOG_DIR   Zielverzeichnis (Default: ./data/logs)
  REMOTE_DIR      SFTP-Verzeichnis (Default: /logs)
  CONTAINER       Docker-Container fuer den Ingest (Default: weblog-analysis)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-ingest) DO_INGEST=0 ;;
    --dry-run|-n) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unbekannte Option: $1" ;;
  esac
  shift
done

load_env() {
  [ -f "$ENV_FILE" ] || die "$ENV_FILE fehlt (SFTP_HOST, SFTP_USER, SFTP_PASS)"
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  : "${SFTP_HOST:?SFTP_HOST fehlt in $ENV_FILE}"
  : "${SFTP_USER:?SFTP_USER fehlt in $ENV_FILE}"
  : "${SFTP_PASS:?SFTP_PASS fehlt in $ENV_FILE}"
}

setup_netrc() {
  NETRC_DIR=$(mktemp -d)
  chmod 700 "$NETRC_DIR"
  local netrc=$NETRC_DIR/netrc
  ( umask 077; printf 'machine %s\nlogin %s\npassword %s\n' \
      "$SFTP_HOST" "$SFTP_USER" "$SFTP_PASS" > "$netrc" )
  chmod 600 "$netrc"
  CURL+=(--netrc-file "$netrc")
}

url() { printf 'sftp://%s%s' "$SFTP_HOST" "$1"; }

# Das Hosting rotiert wochenweise: nach dem Rotationslauf liegt die Vorwoche mal als
# ein Bundle (access.log.45.gz) und mal als Tagesdateien (access.log.45.1.gz ...)
# vor. Beide Formen enthalten dieselben Zeilen. Sobald lokal Tagesdateien einer
# Woche existieren, wird das Wochen-Bundle uebersprungen, sonst wuerde die
# Woche doppelt gezaehlt.
is_weekly_bundle() {
  [[ $1 =~ ^access\.log\.([0-9]+)\.gz$ ]]
}

# Zielpfad fuer einen Remote-Namen. Liegt die unkomprimierte Variante lokal
# bereits vor, wird ein Remote-.gz dorthin entpackt: Ingest identifiziert
# Dateien ueber den Basename, sonst wuerde dieselbe Datei doppelt importiert
# (access.log.46.6 vs. access.log.46.6.gz).
target_for() {
  local name=$1 plain=${1%.gz}
  if [ "$plain" != "$name" ] && [ -e "$FETCH_LOG_DIR/$plain" ]; then
    printf '%s' "$plain"
  else
    printf '%s' "$name"
  fi
}

download() {
  local name=$1 target=$2 tmp out
  if [ "$DRY_RUN" = 1 ]; then
    info "wuerde laden: $name -> $target"
    return 0
  fi
  tmp=$(mktemp "$FETCH_LOG_DIR/.fetch.XXXXXX")
  if ! "${CURL[@]}" -o "$tmp" "$(url "$REMOTE_DIR/$name")"; then
    rm -f "$tmp"
    die "Download fehlgeschlagen: $name"
  fi
  if [ "$target" != "$name" ]; then
    out=$(mktemp "$FETCH_LOG_DIR/.fetch.XXXXXX")
    if ! gunzip -c "$tmp" > "$out"; then
      rm -f "$tmp" "$out"
      die "gunzip fehlgeschlagen: $name"
    fi
    rm -f "$tmp"
    tmp=$out
  fi
  chmod 0664 "$tmp" 2>/dev/null || true
  chgrp users "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$FETCH_LOG_DIR/$target"
  info "geladen: $name -> $target ($(stat -c%s "$FETCH_LOG_DIR/$target") Bytes)"
}

main() {
  load_env
  : "${FETCH_LOG_DIR:=$ROOT/data/logs}"
  : "${REMOTE_DIR:=/logs}"
  : "${CONTAINER:=weblog-analysis}"
  mkdir -p "$FETCH_LOG_DIR" "$STATE_DIR"

  exec 9>"$LOCK"
  flock -n 9 || { info "ein anderer Lauf ist aktiv, Abbruch"; exit 0; }

  setup_netrc

  local listing
  listing=$("${CURL[@]}" "$(url "$REMOTE_DIR/")") ||
    die "SFTP-Listing fehlgeschlagen (Host-Key in ~/.ssh/known_hosts?)"

  declare -A seen=()
  if [ -f "$MANIFEST" ]; then
    while IFS=$'\t' read -r m_name m_sig; do
      [ -n "$m_name" ] && seen[$m_name]=$m_sig
    done < "$MANIFEST"
  fi

  local new_manifest fetched=0
  new_manifest=$(mktemp)

  local perms _links _owner _group size mon day tim name sig target week daily
  while read -r perms _links _owner _group size mon day tim name; do
    [ -n "${name:-}" ] || continue
    # Nur regulaere Dateien; keine Symlinks (access.log.current) oder Ordner.
    [ "${perms:0:1}" = "-" ] || continue
    case "$name" in
      access.log.*) ;;
      *) continue ;;
    esac
    if is_weekly_bundle "$name"; then
      week=${BASH_REMATCH[1]}
      local daily
      daily=$(find "$FETCH_LOG_DIR" -maxdepth 1 -name "access.log.$week.[0-9]*" -print -quit)
      if [ -n "$daily" ]; then
        printf '%s\t%s\n' "$name" "skipped:weekly-bundle" >> "$new_manifest"
        continue
      fi
    fi
    sig="$size $mon $day $tim"
    printf '%s\t%s\n' "$name" "$sig" >> "$new_manifest"

    target=$(target_for "$name")
    if [ "${seen[$name]:-}" = "$sig" ] && [ -e "$FETCH_LOG_DIR/$target" ]; then
      continue
    fi
    # Erstlauf ohne Manifest: lokal vorhandene, gleich grosse Datei als
    # bekannt uebernehmen statt den ganzen Bestand erneut zu laden.
    if [ -z "${seen[$name]:-}" ] && [ -f "$FETCH_LOG_DIR/$name" ] &&
       [ "$(stat -c%s "$FETCH_LOG_DIR/$name")" = "$size" ]; then
      continue
    fi
    download "$name" "$target"
    fetched=$((fetched + 1))
  done <<< "$listing"

  if [ "$DRY_RUN" = 0 ]; then
    mv -f "$new_manifest" "$MANIFEST"
  else
    rm -f "$new_manifest"
  fi

  info "neue/geaenderte Dateien: $fetched"

  if [ "$DO_INGEST" = 0 ] || [ "$DRY_RUN" = 1 ]; then
    return 0
  fi
  if ! docker ps --filter "name=^${CONTAINER}$" --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    info "warnung: Container $CONTAINER laeuft nicht, Ingest uebersprungen"
    return 0
  fi
  info "starte Ingest"
  docker compose -f "$COMPOSE_FILE" exec -T "$CONTAINER" python -m app.ingest
}

main "$@"
