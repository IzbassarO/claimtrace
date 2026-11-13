# Deploy (Vultr Compute, Docker Compose)

Three containers on one VM, compose project `claimtrace-hg` (name pinned in `docker-compose.yml`,
so volumes are always `claimtrace-hg_*` whatever the checkout directory):

| service  | what                                         | ports              | healthcheck                       |
|----------|----------------------------------------------|--------------------|-----------------------------------|
| `app`    | uvicorn, non-root, mem cap 1.5 GB            | 8000, internal     | `GET /healthz`                    |
| `grobid` | `grobid/grobid:0.9.1-crf`, mem cap 4.5 GB    | 8070, internal     | `GET /api/isalive`                |
| `caddy`  | `caddy:2.10`, automatic HTTPS for `$DOMAIN`  | 80, 443, 443/udp   | admin API `127.0.0.1:2019/config/` |

Start order: `grobid` started → `app` healthy → `caddy`.

Volumes: `app_cache` (finished reports in `/cache/reports`, OpenAlex cache and daily search
counter: **back this up**), `caddy_data` (certificates), `caddy_config`.

**Status of each step** (as of this PR): **[verified locally]** = run on macOS with Docker Desktop
(compose v5) against this exact `docker-compose.yml`, `DOMAIN=localhost`; **[reviewed only]** =
needs a real Ubuntu VM (no Vultr credits yet). Local verification ran `app` + `caddy` only,
reusing an already-running `grobid` container; PDF parsing was not exercised end to end.

## 1. Create the server — [reviewed only]

- Vultr → Deploy → Cloud Compute (shared CPU is fine), **8 GB RAM** (GROBID peaks at ~4 GB,
  the app at ~0.7 GB; compose caps them at 4.5 GB and 1.5 GB), ≥ 2 vCPU, ≥ 50 GB disk
  (images ~4 GB), **Ubuntu 24.04 LTS x64**, add your SSH key.
- Note the public IP, e.g. `203.0.113.7`.

## 2. Domain — [reviewed only]

Either point an A record of your domain at the IP, or use sslip.io with no DNS work:
`DOMAIN=203-0-113-7.sslip.io` (IP with dashes). Caddy gets a Let's Encrypt certificate for it
on first start; ports 80 and 443 must be reachable from the internet for that.

## 3. Docker, firewall, swap — [reviewed only]

```bash
ssh root@203.0.113.7
apt-get update && apt-get -y upgrade
curl -fsSL https://get.docker.com | sh          # Docker Engine + compose plugin
docker compose version                          # needs v2.20+ (healthcheck start_interval: Engine 25+)
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 443/udp
ufw --force enable && ufw status
# optional safety net for builds while GROBID is running
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

Note: ports published by Docker bypass `ufw`. That is fine here because only `caddy` publishes
ports (80/443); `app` and `grobid` are reachable only on the compose network. Do not add
`ports:` to them. If you use a Vultr firewall group instead, open 22/tcp, 80/tcp, 443/tcp,
443/udp there.

## 4. Code and `.env` — [reviewed only]

```bash
# on the server
git clone https://github.com/IzbassarO/claimtrace.git /opt/claimtrace
# on your machine: fill in .env from .env.example (all keys, CONTACT_EMAIL, DOMAIN=...)
scp .env root@203.0.113.7:/opt/claimtrace/.env
ssh root@203.0.113.7 'chmod 600 /opt/claimtrace/.env && ls -l /opt/claimtrace/.env'
```

- `.env` is read by compose twice: as `env_file` for `app`, and for `${DOMAIN}` in `caddy`.
  It is excluded from the image (`.dockerignore`) and from git.
- `GROBID_URL` and `CACHE_DIR` in `.env` are ignored in compose; they are set to
  `http://grobid:8070` and `/cache`.
- Never run `docker compose config` without `-q` on the server: it prints the resolved
  environment, keys included.

## 5. Start — [verified locally] (app + caddy; grobid image reused, not freshly pulled)

```bash
cd /opt/claimtrace
docker compose config -q && echo compose OK
docker compose up -d --build       # first build takes several minutes (deps + embedding model)
docker compose ps                  # all three "healthy"; grobid needs ~1 min, caddy waits for app
docker compose exec app python -m claimtrace.core.settings   # prints "settings OK" or variable names only
docker compose logs caddy | grep -i "certificate obtained"   # [reviewed only] Let's Encrypt (not testable locally)
```

## 6. Smoke test — [verified locally]

```bash
scripts/smoke.sh https://$DOMAIN      # from the server (curl + python3 are on Ubuntu) or your machine
```

One command, exits non-zero on the first failure, prints HTTP status and time per step:
`/healthz` → submit a tiny manuscript → poll until `done` → `/api/jobs/<id>` contains the report
→ permalink `/r/<id>` returns the page → `export.md` is non-empty. It also fails if the result is
*degraded* (the sample reference is not `verified`, or the claim gets only
`insufficient_evidence`): providers fail soft, so this is how a wrong or expired API key shows up.
LLM variance can, rarely, trip the claim check: rerun once before debugging. Takes ~25 s.
Last line is `report: <id>`, use it to open `https://$DOMAIN/r/<id>`.

## Logs — [verified locally]

```bash
docker compose logs -f app
docker compose logs --tail 100 grobid caddy
# secret check, prints a count only (expect 0)
docker compose logs app caddy | grep -cE 'api_key=|sk-|Bearer '
```

Verified: after a normal job, a garbage PDF, a bad file type, an unknown job, an invalid chat body,
and a run with fake keys (`sk-...`, failing LLM calls and a failing chat), the count was 0 and the
fake key values never appeared. Caddy writes no access log by default.

## Update — [verified locally] (rebuild + recreate of app, caddy waiting for app healthy)

```bash
cd /opt/claimtrace
git pull
docker compose up -d --build      # rebuilds app; grobid and caddy keep running
scripts/smoke.sh https://$DOMAIN
docker image prune -f
```

Running jobs live in memory: an update or restart drops jobs in progress. Finished reports are
on the `app_cache` volume and survive `restart`, rebuilds and `docker compose down`
(verified: same report JSON and `/r/<id>` 200 after `docker compose restart app` and after
removing and recreating the `app` and `caddy` containers). **`docker compose down -v` deletes
all reports and certificates — never use `-v`.**

## Backup and restore of `app_cache` — [verified locally]

```bash
cd /opt/claimtrace
# backup (app keeps running; streams a tarball to the host)
docker compose exec -T app tar czf - -C /cache . > app_cache-$(date +%F).tgz
# restore (stop app, extract into the volume as the app user, start again)
docker compose stop app
docker compose run --rm --no-deps -T app tar xzf - -C /cache < app_cache-2026-11-13.tgz
docker compose start app
```

Verified locally: backup, delete one report (`/api/jobs/<id>` → 404), restore → 200 again.
Restore adds/overwrites files and does not delete newer ones. Copy backups off the server
(`scp root@203.0.113.7:/opt/claimtrace/app_cache-*.tgz .`). Certificates (`caddy_data`) need
no backup: Caddy re-issues them.

## Rotate API keys — [partly verified]

1. Create the new key at the provider (Anthropic, Moonshot, OpenAlex, Semantic Scholar).
2. Edit `/opt/claimtrace/.env` on the server (or `scp` a new one and `chmod 600` again).
3. Apply: `docker compose up -d --force-recreate app`. `docker compose restart` does **not**
   re-read `.env`. Jobs in progress are dropped; reports stay.
4. `docker compose exec app python -m claimtrace.core.settings` → `settings OK`,
   then `scripts/smoke.sh https://$DOMAIN` (fails as *degraded* if a key is wrong).
5. Revoke the old key at the provider.

Verified locally: recreating `app` with different key values takes effect, and a wrong LLM key
makes the smoke test fail as degraded. Editing the real `.env` on a server: reviewed only.

## Local run (Mac / Linux) — [verified locally]

Put `DOMAIN=localhost` in `.env` (or `export DOMAIN=localhost`; the shell wins over `.env`),
then `docker compose up -d --build`. Caddy serves `https://localhost` with its own local CA
(the browser warns), so the smoke test skips TLS checks:
`SMOKE_INSECURE=1 scripts/smoke.sh https://localhost`. Start only part of the stack with e.g.
`docker compose up -d --no-deps --build app caddy`.

`grobid/grobid:0.9.1-crf` is multi-arch (amd64 + arm64) and runs natively on Apple Silicon.
Older GROBID tags (≤ 0.8.x) and every `-full` tag are amd64-only; on an arm64 Mac they need
`platform: linux/amd64` and run slowly under emulation. Do not use them.
