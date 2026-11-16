# AGENTS.md – weblog-analysis

Analyse-Tool fuer Apache/nginx-Access-Logs im combined-Format mit Vhost-Feld. FastAPI +
SQLite + statisches Dashboard, Auslieferung als Docker-Container. Kein Watcher: Ingest
laeuft per CLI oder Knopf im UI. Maschinen-spezifische Pfade und Hosts stehen in der
gitignorierten `AGENTS.local.md`.

## Befehle

Alles im Projekt-venv, niemals global:

```bash
python3 -m venv .venv                     # einmalig
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check .                    # muss gruen sein
.venv/bin/ruff format --check .           # muss gruen sein
.venv/bin/pytest -q                       # muss gruen sein
```

Ingest und Server lokal:

```bash
LOG_DIR=/path/to/logs DB_PATH=./data/weblog.sqlite \
  .venv/bin/python -m app.ingest            # --rebuild, --no-self, --log-dir, --db

LOG_DIR=/path/to/logs DB_PATH=./data/weblog.sqlite \
  .venv/bin/python -m app                   # http://127.0.0.1:8095
```

Docker:

```bash
docker compose up -d --build
docker compose exec weblog-analysis python -m app.ingest
```

Logs per SFTP holen (`SFTP_*` in `.env`, Ziel per `FETCH_LOG_DIR`):

```bash
scripts/fetch-logs.sh              # holen + Ingest im Container
scripts/fetch-logs.sh --no-ingest  # nur holen
scripts/fetch-logs.sh --dry-run    # nur anzeigen
```

Das Skript ist auf Hoster ausgelegt, die taeglich kurz nach Mitternacht rotieren
(23:58–00:02). Es laesst Wochen-Bundles (`access.log.45.gz`) aus, sobald die
Tagesdateien der Woche lokal liegen, und entpackt ein Remote-`.gz`, wenn der
unkomprimierte Name schon existiert (Schutz vor Doppel-Import ueber den Basename).

## Daten Fakten (wichtig)

- Log-Format: Apache/nginx combined **plus Vhost als Feld 11**. Parser-Regex deckt praktisch
  100 % ab; die wenigen Sonderfaelle (escaped Quotes im Referer) werden ebenfalls geparst.
- **IPs sind in den vorgesehenen Logs vom Provider anonymisiert:** IPv4 endet auf `.0`, IPv6
  ist aufs /48 gekuerzt. Bei unmaskierten Logs greift diese Annahme nicht.
  Ein "User"/"Visitor" ist daher immer ein **Netz** (/24 bzw. /48), nie eine echte Person.
  Diese Einschraenkung im UI und in Antworten offen benennen, nicht als echte User-IP verkaufen.
- Zeitstempel werden als **lokale Wanduhrzeit** gespeichert (TZ-Offset wird nicht angewendet),
  damit Tages-/Wochengrenzen dem entsprechen, was der Betreiber erwartet.
- Datei-Identitaet laeuft ueber den **Basename** (`_file_key`), damit derselbe Logfile unter
  Host-Pfad und Container-Pfad (`/logs`) nicht doppelt importiert wird.

## Architektur

- `app/parser.py` – Regex-Parser, Netz-Maskierung, Vhost-Normalisierung (Aliase)
- `app/classify.py` – Kategorien: browser, search_bot, ai_bot, monitoring, scraper, scanner,
  empty, other. UA-Regeln plus Pfad-Signale (`SCANNER_PATTERNS`).
- `app/ingest.py` – inkrementeller Import, gz/plain, File-Tracking, Re-Read geaenderter Dateien
- `app/queries.py` – Wochen-/Vhost-/Netz-/Bot-Aggregationen (reines SQL).
  Statusfilter ueber `status_class` (`2xx|3xx|4xx|5xx`) laeuft durch alle
  Aggregationen inkl. `week_overview` und `net_detail`.
- `app/main.py` – FastAPI-Routen + statisches Dashboard
- `app/static/index.html` – Vanilla-JS-Dashboard, inline SVG-Charts, keine externen CDNs
- `app/selfip.py` – eigene Netze aus der dyndns-updater `/history`-API

## Geo (vorbereitet, nicht aktiv)

`requests` hat bereits `country_code`, `region`, `city`, `asn` (leer). Spaeter per MaxMind
GeoLite2 fuellen; Genauigkeit bleibt netzbasiert (/24 bzw. /48).

## Nie machen

- Keine echten Secrets/Keys, Kunden- oder Maschinendaten committen. `.env` und
  `AGENTS.local.md` sind gitignored, `.env.example` bleibt Platzhalter. Test-Fixtures
  nutzen ausschliesslich Dokumentations-Netze (RFC 5737) und `example.*`-Domains.
- Keine externen Chart-/JS-CDN-Abhaengigkeiten einfuehren; das Dashboard muss offline laufen.
- Keine voreiligen Commits oder Pushes ohne ausdrueckliche Aufforderung.
