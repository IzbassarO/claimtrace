#!/usr/bin/env bash
# End-to-end smoke test of a running ClaimTrace; exits non-zero on the first failed check.
# Checks: /healthz, a tiny manuscript job until done, /api/jobs/<id> has the report,
# the permalink /r/<id> returns 200, export.md is non-empty. Prints timings per step.
# Usage: scripts/smoke.sh https://example.com   (needs curl and python3)
# SMOKE_INSECURE=1 skips TLS verification (local Caddy CA on https://localhost).
# SMOKE_TIMEOUT=<s> caps the job wait (default 300). Prints the job id last: "report: <id>".
# Also fails when the result is degraded (sample reference unverified or claim without verdict),
# which is how broken API keys show up.
set -euo pipefail
BASE="${1:?usage: $0 <base_url>}"
BASE="${BASE%/}"
TIMEOUT="${SMOKE_TIMEOUT:-300}"
CURL=(curl -sS --max-time 30)
[[ "${SMOKE_INSECURE:-}" == 1 ]] && CURL+=(-k)
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
T0=$SECONDS

fail() { echo "FAIL: $*" >&2; exit 1; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" <"$TMP"; }
# get NAME URL [curl args...]: body into $TMP, fails unless HTTP 200, prints status + time
get() {
  local name=$1 url=$2 out code t
  shift 2
  out=$("${CURL[@]}" -o "$TMP" -w '%{http_code} %{time_total}' "$@" "$url") || fail "$name: curl error"
  read -r code t <<<"$out"
  [[ "$code" == 200 ]] || fail "$name: HTTP $code $(head -c 300 "$TMP")"
  printf '%-12s %s %6.2fs\n' "$name" "$code" "$t"
}

get healthz "$BASE/healthz"
[[ "$(json 'd["status"]')" == ok ]] || fail "healthz: unexpected body"

TEXT='Residual connections make it possible to train networks with more than 100 layers [1].

References
[1] He K, Zhang X, Ren S, Sun J. Deep residual learning for image recognition. CVPR 2016:770-778. doi:10.1109/CVPR.2016.90'

get submit "$BASE/api/jobs" -X POST --form-string "text=$TEXT"
ID=$(json 'd["id"]')
echo "job: $ID"

start=$SECONDS
deadline=$((SECONDS + TIMEOUT))
while :; do
  "${CURL[@]}" -fo "$TMP" "$BASE/api/jobs/$ID" || fail "poll: request failed"
  STATUS=$(json 'd["status"]')
  echo "  $STATUS $(json 'd.get("stage") or ""') $(json 'd.get("done")')/$(json 'd.get("total")')"
  case "$STATUS" in
    done) break ;;
    error) fail "job failed: $(json 'd.get("error")')" ;;
  esac
  ((SECONDS < deadline)) || fail "job timed out after ${TIMEOUT}s"
  sleep 3
done
echo "job done in $((SECONDS - start))s"

get report "$BASE/api/jobs/$ID"
python3 -c "import json,sys; r=json.load(sys.stdin)['report']; assert r and r['references'], 'no references'" <"$TMP" \
  || fail "report: missing or has no references"
echo "summary: $(json 'json.dumps(d["report"]["summary"])')"
# Providers fail soft (unchecked / insufficient_evidence), so a broken key still yields "done".
# This well-known paper must verify and its claim must get a real verdict.
[[ "$(json 'd["report"]["summary"].get("references", {}).get("verified", 0)')" != 0 ]] \
  || fail "degraded: reference not verified (metadata sources down or OPENALEX/CONTACT config?)"
[[ "$(json 'sum(n for v, n in d["report"]["summary"].get("claims", {}).items() if v != "insufficient_evidence")')" != 0 ]] \
  || fail "degraded: claim got no verdict (LLM keys/providers? see: docker compose logs app)"
get permalink "$BASE/r/$ID"
grep -qi '<html' "$TMP" || fail "permalink: not an HTML page"
get export.md "$BASE/api/jobs/$ID/export.md"
[[ -s "$TMP" ]] || fail "export.md: empty"

echo "OK in $((SECONDS - T0))s"
echo "report: $ID"
