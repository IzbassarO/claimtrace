# Deploy (Vultr Compute, Docker Compose)

Three containers on one VM: `app` (uvicorn, port 8000, internal), `grobid` (CRF image
`grobid/grobid:0.9.1-crf`, port 8070, internal), `caddy` (80/443, automatic HTTPS for `$DOMAIN`).

## 1. Create the server

- Vultr → Deploy → Cloud Compute (shared CPU is fine), **≥ 4 GB RAM** (GROBID takes ~2 GB),
  ≥ 2 vCPU, Ubuntu 24.04 LTS x64, add your SSH key.
- Note the public IP, e.g. `203.0.113.7`.

## 2. Domain

Either point an A record of your domain at the IP, or use sslip.io with no DNS work:
`DOMAIN=203-0-113-7.sslip.io` (IP with dashes). Caddy gets a Let's Encrypt certificate for it.

## 3. Docker and firewall

```bash
ssh root@203.0.113.7
curl -fsSL https://get.docker.com | sh
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 443/udp && ufw --force enable
```

(If the Vultr firewall group is used instead, open the same ports there.)

## 4. Code and `.env`

```bash
# on the server
git clone https://github.com/<owner>/<repo>.git /opt/claimtrace
# on your machine: copy your filled-in .env (see .env.example; set DOMAIN there)
scp .env root@203.0.113.7:/opt/claimtrace/.env
ssh root@203.0.113.7 'chmod 600 /opt/claimtrace/.env'
```

`GROBID_URL` in `.env` is ignored in compose; it is set to `http://grobid:8070`.

## 5. Start

```bash
cd /opt/claimtrace
docker compose up -d --build
docker compose ps          # app and grobid become "healthy" (grobid needs ~1 min)
docker compose run --rm --no-deps app python -m claimtrace.core.settings   # "settings OK"
```

## 6. Smoke test

```bash
scripts/smoke.sh https://$DOMAIN      # from the server or your machine
```

Prints `healthz`, the job id, progress lines and the summary; exit code 0 on success.

## Logs

```bash
docker compose logs -f app
docker compose logs --tail 100 grobid caddy
```

## Update

```bash
cd /opt/claimtrace
git pull
docker compose up -d --build      # rebuilds app; grobid and caddy keep running
docker image prune -f
```

Jobs are in memory: a rebuild drops running jobs. Certificates persist in the `caddy_data` volume.

## Local run (Mac / Linux)

Put `DOMAIN=localhost` in `.env`, then `docker compose up -d --build`. Caddy serves
`https://localhost` with its own local CA (the browser warns), so the smoke test skips TLS checks:
`SMOKE_INSECURE=1 scripts/smoke.sh https://localhost`.

`grobid/grobid:0.9.1-crf` is multi-arch (amd64 + arm64) and runs natively on Apple Silicon.
Older GROBID tags (≤ 0.8.x) and every `-full` tag are amd64-only; on an arm64 Mac they need
`platform: linux/amd64` and run slowly under emulation. Do not use them.
