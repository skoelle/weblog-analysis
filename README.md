# weblog-analysis

Web log analysis for Apache/nginx **combined** logs that carry the vhost as an
extra field. It shows how your sites are visited per week, how many distinct
networks (visitors) you have, which bots hit you, and which paths they request.

It was written against access logs from a shared host that appends the vhost to
every line and anonymises the client IP before it is written:

- IPv4 is masked to the last octet (`.0`)
- IPv6 is truncated to its /48 prefix

Parsing such logs is the default assumption. If your provider does not mask the
IPs the tool still works, but "visitors" then mean whatever the raw address
resolution gives you. For anonymised input a "visitor" always means **a network
(/24 or /48)**, never a real person — the UI states this openly.

## What it shows

- Three tabs: **Dashboard** (always unfiltered), **Analyse** (same panels with
  the filters below applied) and **Settings** (fetch/ingest actions plus a
  read-only overview of configuration, data volume and free disk space)
- Weekly requests and unique networks with human/bot/scanner split
- Category breakdown (human, search bots, AI bots, monitoring, scrapers, scanners, no UA)
- Per-vhost traffic (aliases merged: `www.` is stripped; further mappings via
  `VHOST_ALIASES`, e.g. `shop.example.org=example.org`)
- Top bots by name, top paths, top networks with drill-down
- Status codes and top referers
- Your own traffic can be excluded (networks learned from the dyndns-updater
  history API)
- Status filter (all / 2xx / 3xx / 4xx / 5xx) applied everywhere, so the noisy
  404 scan traffic can be hidden with one click

## Quick start (local)

For development run the app in the project venv — no Docker needed:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# fill in SFTP_HOST, SFTP_USER, SFTP_PASS, SFTP_HOST_KEY (see below)
./run.sh
```

`./run.sh` loads `.env` (SFTP access, vhost aliases) but uses the local paths
`data/logs` and `data/weblog.sqlite`, and keeps the daily fetch scheduler off.
Other subcommands: `./run.sh ingest`, `./run.sh fetch`, `./run.sh retention`.
Open <http://127.0.0.1:8095>.

## Quick start (Docker)

The compose file pulls the published image, so no checkout or build is needed.
Copy the example first — the real `docker-compose.yml` is not tracked:

```bash
cp .env.example .env
# fill in SFTP_HOST, SFTP_USER, SFTP_PASS, SFTP_HOST_KEY (see below)
cp docker-compose.example.yml docker-compose.yml
docker compose pull
docker compose up -d
docker compose exec weblog-analysis python -m app.fetch
```

`app.fetch` downloads new logs into `LOG_DIR` (`/app/data/logs` by default) and
then ingests them. Open <http://localhost:8095>; the **Fetch** and **Ingest**
buttons in the header do the same on demand. Next to the Fetch button the header
shows when the last fetch ran and whether it succeeded (green dot = ok, red dot
= failed, grey = no run yet); the tooltip carries the counters and the error
message if there was one.

The **Settings** tab also lists the effective runtime configuration (paths,
host/port, timezone, SFTP target and host-key mode, fetch schedule, retention),
the ingested data volume (requests, networks, weeks, tracked files, failed
lines, database/WAL size), the own networks used by the self-traffic exclusion
(with their source `config`/`history` and the requests flagged as own) and the
free space of every filesystem behind `LOG_DIR` and the database path. Password
and host-key values are never exposed.

With `FETCH_SCHEDULE=01:30` the container fetches on its own once a day, which
fits hosts that rotate logs shortly after midnight. The clock comes from `TZ`;
without it the container runs in UTC and the slot would fire an hour late.

### Published image

Every push to `main` and every `v*` tag builds a container image:

```bash
docker run -d --name weblog-analysis \
  -p 8095:8095 \
  -v /path/to/data:/app/data \
  --env-file .env \
  ghcr.io/skoelle/weblog-analysis:latest
```

### Deployment and updates

The compose service carries the `com.centurylinklabs.watchtower.enable=true`
label, so a Watchtower running in `--label-enable` mode picks it up.

After a build, the workflow optionally calls a deploy webhook: set the
repository secrets `WEBHOOK_URL` and `WEBHOOK_TOKEN` (a Watchtower HTTP API
token, sent as `Authorization: Bearer`). They are repository secrets, **not**
`.env` entries. Without `WEBHOOK_URL` the step is skipped.

Behaviour of that call:

- Only the `main` build fires it. A `v*` tag build publishes version tags while
  `:latest` is already current, so calling the webhook there would always
  report no update.
- The step fails on a non-2xx reply, on `summary.failed > 0`, or when the
  webhook is unreachable.
- `summary.updated == 0` is only a warning: a rebuild with an unchanged digest
  legitimately updates nothing.

Restarting the container is safe. The ingest commits per file, downloads are
written to a temporary file and renamed atomically, and the database uses WAL,
so a restart between files leaves a consistent state. A fetch that was running
is simply repeated on the next schedule or button press.

## Where logs come from

`LOG_DIR` is the only input of the tool: a directory containing `access.log*`
files. How they get there is up to you.

| Producer | Setup |
|---|---|
| SFTP fetch (built in) | set `SFTP_*`; files land in `LOG_DIR` next to the database |
| Bind mount | uncomment the `LOG_HOST_DIR` volume in `docker-compose.example.yml`, set `LOG_DIR=/logs` |
| Copy | `docker cp access.log.1 weblog-analysis:/app/data/logs/` or drop files into the data volume |

The SFTP fetch needs a verified host key. Either pin the fingerprint:

```bash
ssh-keyscan -t ed25519 sftp.example.org | ssh-keygen -lf -
# SFTP_HOST_KEY=SHA256:...
```

or point `SFTP_KNOWN_HOSTS` at a `known_hosts` file. Without one of the two the
fetch refuses to connect — there is no automatic trust on first use.

### Deduplication

Hosts overlap their files: a weekly bundle contains the same lines as the split
daily files, and a log that was half-downloaded earlier shows up again in full
later. Ingestion therefore works on line signatures, not file names:

- Per signature the database keeps the highest occurrence count seen in any
  single file.
- Overlaps between files count once.
- Repeated identical lines inside one file are real requests (scanner bursts do
  this) and are preserved.

So fetching the weekly bundle next to its daily files, or re-reading a partial
file once it is complete, never changes the totals. The numbers stay stable no
matter in which order files arrive.

### Retention

Only raw log files are cleaned up, never database rows: the weekly history in
SQLite is the product, the logs are just the re-fetchable input. With
`FILE_RETENTION_DAYS` (default `400`, `0` disables it) a file is deleted once
its **newest log line** is older than the cutoff. The file mtime is not used
because it resets on every download.

The cleanup runs automatically after a successful fetch and ingest, and can be
triggered by hand:

```bash
python -m app.retention --dry-run          # list candidates only
python -m app.retention --days 90          # override the configured value
```

Two consequences worth knowing:

- The fetch applies the same cutoff using the remote file time, so a pruned
  file is not downloaded again on the next run. `--force` does not override
  this.
- **With active retention, `--rebuild` becomes destructive.** It drops the
  database and re-imports from disk, but weeks whose files were pruned and also
  no longer exist on the host are gone for good.

`VACUUM` is not part of the pipeline: SQLite reuses free pages on the next
insert, and the freelist stays flat during normal operation. Only run
`VACUUM` manually after a `--rebuild`, where a large one-off delete leaves a
permanently bloated file.

## Quick start (local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
LOG_DIR=/path/to/logs DB_PATH=./data/weblog.sqlite \
  python -m app.ingest
LOG_DIR=/path/to/logs DB_PATH=./data/weblog.sqlite \
  python -m app
```

## Configuration

All settings come from environment variables (see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `LOG_DIR` | `/app/data/logs` | Directory with `access.log*` files; the only input of the tool |
| `DB_PATH` | `/app/data/weblog.sqlite` | SQLite database file |
| `HISTORY_URL` | empty | dyndns-updater history endpoint for own-IP detection |
| `SELF_NETWORKS` | empty | Extra own networks, comma separated |
| `HOST` / `PORT` | `0.0.0.0` / `8095` | Web server bind |
| `TZ` | container default (UTC) | Timezone for `FETCH_SCHEDULE`, e.g. `Europe/Berlin` |
| `VHOST_ALIASES` | empty | Extra `from=to` mappings, comma separated |
| `SFTP_HOST` / `SFTP_PORT` | empty / `22` | SFTP server for the built-in fetch |
| `SFTP_USER` / `SFTP_PASS` | empty | SFTP credentials |
| `SFTP_HOST_KEY` | empty | Pinned host key fingerprint (`SHA256:...`) |
| `SFTP_KNOWN_HOSTS` | empty | Alternative to `SFTP_HOST_KEY` |
| `SFTP_REMOTE_DIR` | `/logs` | Remote directory scanned for `access.log*` |
| `FETCH_SCHEDULE` | empty | Comma separated `HH:MM` local times for automatic fetches |
| `FETCH_ON_STARTUP` | `false` | Run one fetch when the app starts |
| `FILE_RETENTION_DAYS` | `400` | Delete raw log files older than this; `0` disables |

Either `SFTP_HOST_KEY` or `SFTP_KNOWN_HOSTS` is required; the fetch refuses to
connect without a verified host key.

## Fetching logs over SFTP

```bash
python -m app.fetch                # fetch + ingest
python -m app.fetch --no-ingest    # fetch only
python -m app.fetch --dry-run      # show what would be downloaded
python -m app.fetch --force        # download even unchanged files
```

The same is available as the **Fetch** button and `POST /api/fetch[?force=true]`.
Unchanged files are skipped using a manifest of size and modification time next
to the database (`.fetch-manifest.tsv`); `--force` ignores it.

## Ingest behaviour

- Reads `access.log*` plus `.log`, `.gz`, `.txt` files, transparently gunzipped
- Tracks size and mtime per file; re-running only processes new/changed files
- Changed files are re-read and their old rows replaced
- Lines already imported from another file are skipped (see *Deduplication*);
  repeated identical lines inside one file are kept
- `python -m app.ingest --rebuild` drops everything and re-imports
- `python -m app.ingest --no-self` skips the history API lookup

## API

- `GET /api/health`, `/api/filters`
- `GET /api/weeks[?vhost=…&category=…&status_class=…&exclude_self=true]` (no `week`: the chart always spans all weeks)
- `GET /api/vhosts`, `/api/categories`, `/api/bots`, `/api/status`, `/api/referers`
- `GET /api/paths`, `/api/nets` (filters: `week`, `vhost`, `category`, `status_class`, `exclude_self`, `limit`)
- `GET /api/nets/{net}` with drill-down
- `POST /api/ingest[?rebuild=true]`
- `GET /api/fetch/status` (last fetch run, its counters and error if any)
- `POST /api/fetch[?force=true]`

Interactive docs at `/docs`.

## GeoIP (prepared, not enabled)

The `requests` table already has `country_code`, `region`, `city` and `asn`
columns. A later step can fill them from a MaxMind GeoLite2 database. Accuracy
is limited to the /24 or /48 network anyway.

## Tests

```bash
pytest
ruff check . && ruff format --check .
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Stefan Koelle
(https://stefankoelle.de).
