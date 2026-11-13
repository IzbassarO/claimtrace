#!/usr/bin/env bash
# Smoke test a running ClaimTrace: healthz, submit a tiny manuscript, poll until done.
# Usage: scripts/smoke.sh https://example.com   (needs curl and python3)
# SMOKE_INSECURE=1 skips TLS verification (local Caddy CA on https://localhost).
set -euo pipefail
BASE="${1:?usage: $0 <base_url>}"
BASE="${BASE%/}"
TIMEOUT="${SMOKE_TIMEOUT:-300}"
CURL=(curl -fsS)
[[ "${SMOKE_INSECURE:-}" == 1 ]] && CURL+=(-k)

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

echo "healthz: $("${CURL[@]}" "$BASE/healthz")"

TEXT='Residual connections make it possible to train networks with more than 100 layers [1].

References
[1] He K, Zhang X, Ren S, Sun J. Deep residual learning for image recognition. CVPR 2016:770-778. doi:10.1109/CVPR.2016.90'

ID=$("${CURL[@]}" -X POST "$BASE/api/jobs" --form-string "text=$TEXT" | json 'd["id"]')
echo "job: $ID"

deadline=$((SECONDS + TIMEOUT))
while :; do
  BODY=$("${CURL[@]}" "$BASE/api/jobs/$ID")
  STATUS=$(json 'd["status"]' <<<"$BODY")
  echo "  $STATUS $(json 'd.get("stage") or ""' <<<"$BODY") $(json 'd.get("done")' <<<"$BODY")/$(json 'd.get("total")' <<<"$BODY")"
  case "$STATUS" in
    done) echo "summary: $(json 'json.dumps(d["report"]["summary"])' <<<"$BODY")"; exit 0 ;;
    error) echo "job failed: $(json 'd.get("error")' <<<"$BODY")" >&2; exit 1 ;;
  esac
  if ((SECONDS > deadline)); then echo "timed out after ${TIMEOUT}s" >&2; exit 1; fi
  sleep 3
done
