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

Logs per SFTP holen (`SFTP_*` in `.env`, Ziel ist `LOG_DIR`):

```bash
docker compose exec weblog-analysis python -m app.fetch              # holen + Ingest
docker compose exec weblog-analysis python -m app.fetch --no-ingest  # nur holen
docker compose exec weblog-analysis python -m app.fetch --dry-run    # nur anzeigen
docker compose exec weblog-analysis python -m app.fetch --force      # alles neu laden
```

Der Fetch laeuft auch automatisch gemaess `FETCH_SCHEDULE` (Default `01:30`;
die Rotation endet kurz nach Mitternacht) sowie per **Fetch**-Knopf und
`POST /api/fetch`. Host-Key ist Pflicht: `SFTP_HOST_KEY` oder `SFTP_KNOWN_HOSTS`.

## CI und Deployment

- `.github/workflows/lint.yml` – `ruff check`, `ruff format --check`, `pytest` (PRs und `main`).
- `.github/workflows/build.yml` – baut `ghcr.io/skoelle/weblog-analysis` mit Tags
  `latest` (nur Default-Branch), Semver und `sha`. Laeuft auf `main`-Push und `v*`-Tags.
- Nach dem Push ruft der **Branch**-Run optional einen Deploy-Webhook (Watchtower HTTP-API).
  Secrets: `WEBHOOK_URL`, `WEBHOOK_TOKEN`. Der Tag-Run feuert bewusst nicht, weil `latest`
  dort schon aktuell ist. Fehler nur bei HTTP != 2xx, `summary.failed > 0` oder
  Nichterreichbarkeit; `summary.updated == 0` ist eine Warnung.
- `docker-compose.yml` setzt `com.centurylinklabs.watchtower.enable=true`, damit Watchtower
  den Container im `--label-enable`-Modus aktualisiert.
- Nie Secrets oder Webhook-URLs ins Repo schreiben; sie gehoeren in die GitHub-Secrets.

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
- **Dedupe laeuft ueber Zeilen-Signaturen, nicht ueber Dateinamen.** Pro Signatur
  (`ts, ip, vhost_raw, method, path, status, bytes, referer, ua`, BLAKE2b-16) gilt die
  hoechste Vorkommenszahl einer einzelnen Datei. Ueberlappende Dateien (Wochen-Bundle +
  Tagesdateien, Teil- + Volldatei) zaehlen dadurch einmal, identische Zeilen **innerhalb**
  einer Datei bleiben erhalten. `line_counts` haelt die Zahlen, `requests.sig` die Zuordnung.
- **Retention loescht nur Rohlogs, nie DB-Zeilen.** `FILE_RETENTION_DAYS` (Default 400, 0 = aus)
  prueft das juengste Log-Datum der Datei (`ingested_files.max_day`, nicht die mtime, die
  beim Download zurueckgesetzt wird). Laeuft nach erfolgreichem Fetch+Ingest und im Fetch
  selbst (Remote-Zeit), damit geloeschte Dateien nicht zurueckkommen. Mit aktiver Retention
  ist `--rebuild` endgueltig destruktiv.
- Kein `VACUUM` im Regelbetrieb: SQLite verwendet freie Pages wieder, der Freelist bleibt
  flach. Nur nach einem `--rebuild` manuell sinnvoll.

## Architektur

- `app/parser.py` – Regex-Parser, Netz-Maskierung, Vhost-Normalisierung (Aliase)
- `app/classify.py` – Kategorien: browser, search_bot, ai_bot, monitoring, scraper, scanner,
  empty, other. UA-Regeln plus Pfad-Signale (`SCANNER_PATTERNS`).
- `app/ingest.py` – inkrementeller Import, gz/plain, File-Tracking, Zeilen-Dedupe
- `app/fetch.py` – SFTP-Download (paramiko) mit Manifest, Host-Key-Pflicht, atomarem Schreiben
- `app/scheduler.py` – asyncio-Loop fuer `FETCH_SCHEDULE` (HH:MM, lokale Zeit)
- `app/db.py` – Schema inkl. Migration auf `requests.sig`, `line_counts`, `ingested_files.max_day`
- `app/retention.py` – loescht Rohlogs nach `FILE_RETENTION_DAYS`, `--dry-run` und `--days`
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
- `--rebuild` nur bewusst: es loescht auch Zeilen, deren Quelldatei remote nicht mehr
  existiert (z. B. alte Wochen-Bundles), und die sind danach unwiederbringlich.
