# Deploy (Vultr Compute, Docker Compose)

**Live:** https://66-135-11-202.sslip.io — deployed 2026-11-14 on Vultr Cloud Compute, 4 dedicated
vCPU, 16 GB RAM, 240 GB NVMe, Ubuntu 24.04. Every step marked **[done on Vultr]** was run on that
server as written.

Three containers, compose project `claimtrace-hg` (name pinned, so volumes are always
`claimtrace-hg_*`):

| service  | what | ports | healthcheck |
|----------|------|-------|-------------|
| `app`    | uvicorn, non-root; mem cap `APP_MEM_LIMIT` (default 1.5 GB, Vultr 3 GB) | 8000, internal | `GET /healthz` |
| `grobid` | `grobid/grobid:0.9.1-crf`; mem cap `GROBID_MEM_LIMIT` (default 4.5 GB, Vultr 6 GB) | 8070, internal | `GET /api/isalive` |
| `caddy`  | `caddy:2.10`, automatic HTTPS for `$DOMAIN` | 80, 443, 443/udp | admin API `127.0.0.1:2019/config/` |

Start order: `grobid` started → `app` healthy → `caddy`. All three have `restart: unless-stopped`;
with Docker enabled at boot they come back after a reboot (verified on Vultr).

Volumes: `app_cache` (reports; OpenAlex, Crossref, source and verdict caches; daily search and job
counters; per-country usage counts — **back this up**), `caddy_data` (certificates),
`caddy_config`.

## 1. Server — [done on Vultr]

Cloud Compute, Ubuntu 24.04 x64, your SSH key. Sizing that worked: 4 vCPU / 16 GB (measured peaks
under 5 concurrent jobs: app 1.21 GB, GROBID 5.31 GB; disk used 13 GB incl. 3.3 GB of images). An
8 GB machine works with the default caps and `MAX_RUNNING_JOBS=1`.

## 2. Domain — [done on Vultr]

No DNS work: `DOMAIN=<ip-with-dashes>.sslip.io` in `.env` (here `66-135-11-202.sslip.io`). Caddy
obtained a Let's Encrypt certificate on first start; ports 80/443 must be reachable.

## 3. Hardening, Docker, firewall, swap — [done on Vultr]

```bash
ssh root@<ip>
export DEBIAN_FRONTEND=noninteractive
apt-get update && apt-get -y -o Dpkg::Options::="--force-confold" upgrade
apt-get -y install unattended-upgrades fail2ban ufw curl git
systemctl enable --now unattended-upgrades fail2ban
# SSH keys only. The file name matters: sshd keeps the FIRST value it reads, and the cloud image's
# 50-cloud-init.conf sets "PasswordAuthentication yes", so ours must sort before it.
cat > /etc/ssh/sshd_config.d/00-claimtrace.conf <<EOF
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
sshd -t && systemctl reload ssh && sshd -T | grep -E '^(passwordauthentication|permitrootlogin)'
ufw default deny incoming && ufw default allow outgoing
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 443/udp && ufw --force enable
# 4 GB swap (the Vultr image came with an 8 GB /swapfile: swapoff, delete, recreate)
fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo vm.swappiness=10 > /etc/sysctl.d/99-claimtrace.conf && sysctl -p /etc/sysctl.d/99-claimtrace.conf
curl -fsSL https://get.docker.com | sh && systemctl enable --now docker
reboot   # the upgrade asked for one; it also proves the autostart
```

Ports published by Docker bypass `ufw`; only `caddy` publishes ports, so `app` and `grobid` stay
internal. Do not add `ports:` to them.

## 4. Code, `.env`, `server.env` — [done on Vultr]

The owner copies `.env` (secrets): `scp .env root@<ip>:/opt/claimtrace/.env && chmod 600`. Check
variable *names* only, never values: `cut -d= -f1 /opt/claimtrace/.env`. With `.env` already in
place, clone into the non-empty directory like this:

```bash
cd /opt/claimtrace
git init -q && git remote add origin https://github.com/IzbassarO/claimtrace.git
git fetch origin main && git checkout -f -B main origin/main   # leaves the untracked .env alone
cp deploy/server.env.example server.env                         # non-secret sizing
```

`server.env` values used on Vultr (reasons in the file): `RETRIEVAL_THREADS=2`,
`RETRIEVAL_SLOTS=2` (4 vCPU), `MAX_RUNNING_JOBS=3`, `APP_MEM_LIMIT=3g`, `GROBID_MEM_LIMIT=6g`.
Optional in `.env`: `MAX_JOBS_PER_DAY` (default 300) and `USAGE_TOKEN` (enables
`GET /api/usage` with `Authorization: Bearer <token>`). `GROBID_URL` and `CACHE_DIR` in `.env` are
ignored: compose sets `http://grobid:8070` and `/cache`. Never run `docker compose config` without
`-q` on the server: it prints the resolved environment.

## 5. Start — [done on Vultr]

```bash
cd /opt/claimtrace
# both env files: server.env also drives the memory caps in docker-compose.yml
docker compose --env-file .env --env-file server.env up -d --build
docker compose ps                                           # all three "healthy" (grobid ~30 s)
docker compose exec app python -m claimtrace.core.settings  # "settings OK" (names only otherwise)
docker compose logs caddy | grep -ci "certificate obtained" # 1
curl -s https://$DOMAIN/api/info    # {"max_reports":1000,"jobs_today":N,"daily_limit":300}
```

Measured on Vultr (4 vCPU / 16 GB, cold caches): the 40-reference PDF "Attention Is All You Need"
(58 claims): first reference 18.7 s, first claim 27 s, done **191 s**; app peak 0.84 GB, GROBID
4.36 GB. Five concurrent PDFs (3 running, 2 queued): GAN 144 s, BERT 230 s, ResNet (105 claims)
322 s; queued jobs started as slots freed (queue positions 2 → 1 reported); peaks app 1.21 GB,
GROBID 5.31 GB; no restarts. Log secret check: 0 matches.

## 6. Smoke test — [done on Vultr]

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

## Update — [done on Vultr] (rebuild + recreate of app, caddy waiting for app healthy)

```bash
cd /opt/claimtrace
git fetch origin main && git checkout -f -B main origin/main
docker compose --env-file .env --env-file server.env up -d --build   # rebuilds app
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
