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

## Quick start (Docker)

```bash
cp .env.example .env
# point LOG_HOST_DIR at the directory that holds your access.log* files
docker compose up -d --build
docker compose exec weblog-analysis python -m app.ingest
```

Open <http://localhost:8095>. Press **Ingest** in the header to pick up new or
changed log files at any time.

### Published image

Every push to `main` and every `v*` tag builds a container image:

```bash
docker run -d --name weblog-analysis \
  -p 8095:8095 \
  -v /path/to/logs:/logs:ro \
  -v /path/to/data:/app/data \
  -e TZ=Europe/Berlin \
  ghcr.io/skoelle/weblog-analysis:latest
```

Then run the first import with
`docker exec weblog-analysis python -m app.ingest`.

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
| `LOG_DIR` | `/logs` | Directory with `access.log*` files (gz and plain) |
| `LOG_HOST_DIR` | `./data/logs` | Host directory that compose mounts at `LOG_DIR` |
| `DB_PATH` | `/app/data/weblog.sqlite` | SQLite database file |
| `HISTORY_URL` | empty | dyndns-updater history endpoint for own-IP detection |
| `SELF_NETWORKS` | empty | Extra own networks, comma separated |
| `HOST` / `PORT` | `0.0.0.0` / `8095` | Web server bind |
| `VHOST_ALIASES` | empty | Extra `from=to` mappings, comma separated |
| `SFTP_HOST` / `SFTP_USER` / `SFTP_PASS` | empty | Only used by `scripts/fetch-logs.sh` |
| `FETCH_LOG_DIR` | `./data/logs` | Download target of `scripts/fetch-logs.sh` |
| `REMOTE_DIR` | `/logs` | Remote log directory on the SFTP host |

## Fetching logs over SFTP

If your logs live on a web host you can reach via SFTP, `scripts/fetch-logs.sh`
pulls new and changed `access.log*` files and then runs the ingest inside the
container. It is aimed at hosts that rotate daily shortly after midnight
(observed 23:58–00:02), so a scheduled run around 01:30 sees a complete day:

```bash
scripts/fetch-logs.sh              # fetch + ingest
scripts/fetch-logs.sh --no-ingest  # fetch only
scripts/fetch-logs.sh --dry-run    # show what would be downloaded
```

Credentials come from `${SFTP_ENV_FILE:-.env}` (`SFTP_HOST`, `SFTP_USER`,
`SFTP_PASS`) and only ever live in a temporary netrc. The target directory is
`FETCH_LOG_DIR` (default `./data/logs`) and the remote
directory is `REMOTE_DIR` (default `/logs`). Two details are handled
automatically:

- A weekly bundle (`access.log.45.gz`) is skipped once the split daily files
  (`access.log.45.1.gz`, …) exist locally, since both contain the same lines.
- A remote `.gz` whose uncompressed name already exists locally is gunzipped
  into that name. Ingest keys files by basename, so this avoids double import.

## Ingest behaviour

- Reads `access.log*` plus `.log`, `.gz`, `.txt` files, transparently gunzipped
- Tracks size and mtime per file; re-running only processes new/changed files
- Changed files are re-read and their old rows replaced
- `python -m app.ingest --rebuild` drops everything and re-imports
- `python -m app.ingest --no-self` skips the history API lookup

## API

- `GET /api/health`, `/api/filters`
- `GET /api/weeks[?exclude_self=true]`
- `GET /api/vhosts`, `/api/categories`, `/api/bots`, `/api/status`, `/api/referers`
- `GET /api/paths`, `/api/nets` (filters: `week`, `vhost`, `category`, `status_class`, `exclude_self`, `limit`)
- `GET /api/nets/{net}` with drill-down
- `POST /api/ingest[?rebuild=true]`

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
