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

Entwickeln laeuft **lokal ohne Docker** ueber `./run.sh`. Es laedt `.env`
(SFTP-Zugang, Alias-Einstellungen), benutzt aber lokale Pfade (`data/logs`,
`data/weblog.sqlite`) und laesst den 01:30-Scheduler beim Serverstart aus.
Docker/Compose ist nur fuer den Deploy da.

```bash
./run.sh                     # Server auf http://127.0.0.1:8095
./run.sh ingest              # Logs aus LOG_DIR einlesen (--rebuild, --no-self, ...)
./run.sh fetch               # SFTP holen + Ingest (--dry-run, --force, --no-ingest)
./run.sh retention --days 30 # Rohlogs aufraeumen
```

Ohne `run.sh` geht es auch direkt (dann Pfade explizit setzen, `.env` **nicht**
global source'n, sonst greifen die Container-Pfade `/app/data/...`):

```bash
LOG_DIR=./data/logs DB_PATH=./data/weblog.sqlite \
  .venv/bin/python -m app.ingest            # --rebuild, --no-self, --log-dir, --db

LOG_DIR=./data/logs DB_PATH=./data/weblog.sqlite \
  .venv/bin/python -m app                   # http://127.0.0.1:8095
```

Deploy (nur auf dem Zielhost, `docker-compose.yml` aus dem Beispiel erzeugen):

```bash
cp docker-compose.example.yml docker-compose.yml
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
- `docker-compose.example.yml` setzt `com.centurylinklabs.watchtower.enable=true`, damit
  Watchtower den Container im `--label-enable`-Modus aktualisiert. Im Repo liegt nur das
  Beispiel; die echte `docker-compose.yml` ist gitignored und existiert nur im Deploy.
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
- Eigene Netze (`is_self`) sind **zeitbewusst**: `self_ip_history` haelt die History-Eintraege
  (inkl. exakter eigener IPs aus der API) mit lokaler Wanduhrzeit, `SELF_NETWORKS` gilt immer.
  Der Backfill setzt beim Ingest nur neue Treffer (`is_self=1`) und entfernt nie Flags; kein
  `--rebuild` noetig. Die Log-Zeilen selbst bleiben auf /24 bzw. /48 anonymisiert.
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
- `app/ingest.py` – inkrementeller Import, gz/plain, File-Tracking, Zeilen-Dedupe,
  zeitbewusster Own-Traffic-Backfill
- `app/fetch.py` – SFTP-Download (paramiko) mit Manifest, Host-Key-Pflicht, atomarem Schreiben
- `app/scheduler.py` – asyncio-Loop fuer `FETCH_SCHEDULE` (HH:MM, lokale Zeit)
- `app/db.py` – Schema inkl. Migration auf `requests.sig`, `line_counts`, `ingested_files.max_day`
- `app/retention.py` – loescht Rohlogs nach `FILE_RETENTION_DAYS`, `--dry-run` und `--days`
- `app/info.py` – read-only Infos fuer den Settings-Tab: Runtime/Config, Speicherplatz
  der konfigurierten Pfade, Datenbestand und letzter Fetch. Keine Secrets im Response.
- `app/queries.py` – Wochen-/Vhost-/Netz-/Bot-Aggregationen (reines SQL).
  Statusfilter ueber `status_class` (`2xx|3xx|4xx|5xx`) laeuft durch alle
  Aggregationen inkl. `week_overview` und `net_detail`.
- `app/aggregate.py` – vorberechnetes Dashboard-Aggregat `agg_net` (Grain
  `iso_week, vhost, category, is_self, status, net`) fuer die sechs Dashboard-Panels.
  Wird nach jedem Ingest neu gebaut (transaktional, fehlertolerant), beim Start bei
  Bedarf und per `POST /api/aggregate`/CLI `python -m app.aggregate`. `net` bleibt
  Dimension, damit `COUNT(DISTINCT net)` exakt ist. `top_paths`, `referer_breakdown`
  und `net_detail` laufen weiter live gegen `requests`.
- `app/main.py` – FastAPI-Routen + statisches Dashboard
- `app/static/index.html` – Vanilla-JS-Dashboard, inline SVG-Charts, keine externen CDNs
- `app/selfip.py` – eigene IP-Historie (Zeit + Netz) aus der dyndns-updater
  `/history`-API, Intervall-Matcher

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
